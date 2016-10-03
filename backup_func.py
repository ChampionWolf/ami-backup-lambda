#! /usr/bin/env python

# This script runs backup for all existing instances (except OpsWorks)
# It creates AMI for the instances and cleans up older AMIs using exponential time scale.
#
# The script uses the instance names (the tag "Name") as a source for the backup names.
#
# The script is suited to run by the AWS Lambda. If you want to run it locally, uncomment
# the main function start in the end of the script.
#
# @author Uriel Ben-Iohanan <alterrebe@gmail.com>
#

import datetime
import boto3
import sys
import re

# Configuration settings. Please tune them up
OWNER_ID = '123456789012'
REGION = 'us-east-1'

# Define descriptions for your instances. The key is the instance name (tag "Name"),
# the value is the contents of the description. The script understands if your instances
# are named as 'vpc-xxx' and 'ec2-xxx'. In this case the prefix is stripped before the lookup
DESC_NAMES = {
    'infosrv' : 'Informational Server',
    'exchange' : 'MS Exchange Server',
    'openvpn' : 'OpenVPC Gateway'
}

INSTANCE_FILTERS = [ 
# Uncomment the following line if you want to limit backup with the instances that have
# the specific tag 'Backup' set to 'Yes':
#    {'Name': 'tag-key', 'Values': ['Backup', 'Yes']} 
]

# End of configuration parameters

SNAPSHOT_DESC_PATTERN = re.compile("Created by CreateImage\(i-[a-z0-9]{8,}\) for (ami-[a-z0-9]{8,}) from vol-[a-z0-9]{8,}")
PREFIX_NAMES = { 'ec2': 'EC2', 'vpc': 'VPC' }

ec = boto3.client('ec2', region_name=REGION)


def get_tag(instance, tag_name):
    return next((t['Value'] for t in instance['Tags'] if t['Key'] == tag_name), None)


def format_desc_name(prefix, name):
    if len(prefix) == 0:
        return name
    else:
        return prefix + ' ' + name


def backup_instance(instance, name, state):
    print("+ Create backup of %s (%s), state: %s" % (name, instance['InstanceId'], state))
    ami_name = "%s-%s" % (name, datetime.date.today().strftime('%m%d%y'))
    if name.startswith('ec2-') or name.startswith('vpc-'):
        prefix = PREFIX_NAMES[ name[0:3] ]
        sname = name[4:]
    else:
        prefix = ''
        sname = name

    if sname in DESC_NAMES:
        desc_name = format_desc_name(prefix, DESC_NAMES[sname])
    else:
        cf_stack_name = get_tag(instance, 'aws:cloudformation:stack-name')
        if cf_stack_name is None:
            desc_name = format_desc_name(prefix, 'Unknown')
        else:
            desc_name = format_desc_name(prefix, cf_stack_name)

    ami_desc = "%s as of %s" % (desc_name, datetime.date.today().strftime('%m/%d/%Y'))
    noreboot = state == 'running'
    print("+ Call create_image, Name=%s, Desc=%s, NoReboot=%s" %(ami_name, ami_desc, noreboot))
    image = ec.create_image(InstanceId=instance['InstanceId'], Name=ami_name, Description=ami_desc, NoReboot=noreboot)
    print("+ EC2 response: %s" % str(image))


def remove_backup(image_id, snapshots):
    print("- Remove AMI %s " % image_id)
    ec.deregister_image(ImageId=image_id)
    for s in snapshots:
        print("- Remove snapshot %s" % s)
        ec.delete_snapshot(SnapshotId=s)


# The idea of the algorithm is following:
# For each interval ]2^(n-1), 2^n]  we find the oldest backup and keeps it.
# The rest backups in the interval are removed.
# The process repeats starting from n = MAX_ORDER and till n = 2.
# In result we always (except for the start of the backup) have the following backups:
# - Today (0)
# - Yesterday (1)
# - Day before yesterday (2)
# - Either 3 or 4 days ago (one of them)
# - One for 5,6,7 or 8 days ago
# - One for 9-16 days ago
# - One for 17-32 days ago
# - One for 33-64 days ago
# Few older backups (one per each ~ 2 months) - we may want to remove backups older than 1 year
# if the total number of backups > MAX_ORDER.
# In result we will have no more than 8 + 5 = 13 (or MAX_ORDER + 2 + (365 - 2^MAX_ORDER) / 2^MAX_ORDER in general case)
class BackupSet:
    # The order of the oldest existing backups:
    MAX_ORDER = 6  # limit the backups with 2 months and 6 backups per an image
    DAYS_TO_KEEP_BACKUPS = 365  # We will remove all backups older than that if there are more than MAX_ORDER backups

    def __init__(self, server_name, ami_list, all_snapshots, aws_remove_func):
        # We expect images to be sorted here
        self.name = server_name
        self.images = ami_list
        self.today = datetime.date.today()
        self.backup_num = 0
        self.all_snapshots = all_snapshots
        self.aws_remove_func = aws_remove_func

    def _update_days_ago(self):  # current difference
        self.backup_days_ago = (self.today - self.images[self.backup_num]['created_at']).days

    def _inc_backup_num(self):
        self.backup_num += 1
        if self.backup_num < len(self.images):
            self._update_days_ago()
            return False
        else:
            return True

    def _keep_and_inc(self):
        print("+ Keeping AMI %s (%s) created on %s, %d days ago" %
              (self.images[self.backup_num]['id'],
               self.name, self.images[self.backup_num]['created_at'].isoformat(),
               self.backup_days_ago))
        return self._inc_backup_num()

    def _remove_and_inc(self):
        image = self.images[self.backup_num]
        image_id = image['id']
        print("- Removing AMI %s (%s) created on %s, %d days ago" % (
            image_id, self.name, image['created_at'].isoformat(), self.backup_days_ago))        
        snapshots = []
        if image_id in self.all_snapshots:
            snapshots = self.all_snapshots[image_id]
        self.aws_remove_func(image['id'], snapshots)
        return self._inc_backup_num()

    def remove_old_backups(self):
        if len(self.images) < BackupSet.MAX_ORDER:
            return
        self._update_days_ago()
        print("* Remove old backups of %s, today = %s" % (self.name, self.today.isoformat()))
        # Remove really old backups:
        while (self.backup_days_ago > BackupSet.DAYS_TO_KEEP_BACKUPS and
               len(self.images) - self.backup_num >= BackupSet.MAX_ORDER):
            self._remove_and_inc()

        # Skip backups that are older than 2^MAX_ORDER and newer than DAYS_TO_KEEP_BACKUPS
        upper_bound = pow(2, BackupSet.MAX_ORDER)  # inclusive
        while self.backup_days_ago > upper_bound:
            if self._keep_and_inc():
                return

        # Continue with the algorithm described above
        for n in range(BackupSet.MAX_ORDER, 1, -1):
            lower_bound = pow(2, n - 1)  # non-inclusive
            # Look up for the oldest backup in the range:
            if self.backup_days_ago > lower_bound:
                if self._keep_and_inc():  # keep one
                    return
                while self.backup_days_ago > lower_bound:
                    if self._remove_and_inc():  # remove all others
                        return


# The function collects a map of all existing private images by name
def get_images():
    response = ec.describe_images(Owners=['self'], Filters=[
        {'Name': 'image-type', 'Values': ['machine']},
        {'Name': 'root-device-type', 'Values': ['ebs']},
        {'Name': 'state', 'Values': ['available']}
    ])['Images']

    all_images = dict()
    for i in response:
        if not i['Public']:  # skip public images
            name = i['Name']
            image_id = i['ImageId']
            created_at = datetime.datetime.strptime(i['CreationDate'].split('T')[0], "%Y-%m-%d").date()
            pos = name.rfind('-')
            if pos > 0 and name[pos + 1:].isdigit():  # basic checks
                name = name[0:pos]
                if name not in all_images:
                    all_images[name] = []
                all_images[name].append({'id': image_id, 'created_at': created_at})
    #            resrc = ec2.Image(image_id).get_available_subresources()
    #            print("Image %s (%s): %s" %(name, image_id, str(resrc)))
    # else:
    #           print('AMI name %s does not meet expectations' % name)
    # for k in all_images:
    #    dates=[ r['created_at'] for r in all_images[k] ]
    #    dates.sort()
    #    print("%s: %d images since %s till %s" % (k, len(dates), dates[0].isoformat(), dates[-1].isoformat()))
    # print(all_images)    
    return all_images    


def get_snapshots():    
    response = ec.describe_snapshots(OwnerIds=[ OWNER_ID ], MaxResults=1000,
        Filters=[
            {'Name': 'status', 'Values': ['completed']}
        ]
    )['Snapshots']

    all_snapshots = dict()
    for s in response:
        snapshot_id = s['SnapshotId']
        snapshot_desc = s['Description']
        m = SNAPSHOT_DESC_PATTERN.match(snapshot_desc)
        if m is None:
            print("Skip the snapshot %s: %s" %(snapshot_id, snapshot_desc))
        else:
            ami_id = m.group(1)
            if ami_id not in all_snapshots:
                all_snapshots[ami_id] = []
            all_snapshots[ami_id].append(snapshot_id)
    return all_snapshots


def get_instances():    
    reservations = ec.describe_instances(Filters = INSTANCE_FILTERS).get('Reservations', [])
    instances = sum(
        [
            [i for i in r['Instances']]
            for r in reservations
        ], [])
    return instances


def lambda_handler(event, context):
    all_images = get_images()
    all_snapshots = get_snapshots()
    instances = get_instances()

    print("Number of the Instances : %d" % len(instances))

    today = datetime.date.today()
    for i in instances:
        #    print(i)
        state = i['State']['Name']
        name = get_tag(i, 'Name')
        opsworks_stack = get_tag(i, 'opsworks:stack')
        if opsworks_stack is None:
            # We do NOT backup OpsWorks instances. This one is NOT an OpsWorks one, so print info and start the process
            sys.stdout.write('Instance %s, status: %s - ' % (name, state))
            if name in all_images:
                images = all_images[name]
                images.sort(key=lambda r: r['created_at'])
                dates = [r['created_at'] for r in images]
                print("%d images since %s till %s" % (len(dates), dates[0].isoformat(), dates[-1].isoformat()))
            else:
                images = []
                print("no backup images are found")

            if len(images) > 0 and images[len(images)-1]['created_at'] == today:
                print("- Skip %s (%s) - a fresh backup already exists" % (name, i['InstanceId']))
            else:
                backup_instance(i, name, state)

            backup_set = BackupSet(name, images, all_snapshots, remove_backup)
            backup_set.remove_old_backups()
        else:
            print('Instance %s, status: %s - OPSWORKS (ignored)' % (name, state))


# Uncomment the following line if you want to run the script manually. Do NOT do it if you run it by Lambda
#lambda_handler(None, None)
