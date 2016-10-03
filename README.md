# ami-backup-lambda
AWS EC2 Backup via AMI creation using AWS Lambda service

The Lambda function is intended to run daily on a schedule. It creates backups of all instances 
in the account (both running and stopped) except OpsWorks instances. The backups are plain AMIs
that allows you to restore the failed server by killing it and restarting it from the backup AMI.
The function maintains no more than a defined amount of backup images present in any moment by
removing old AMIs using an exponential scheme: it keeps backups from today, yesterday, and one
backup per the time range between 2^n and 2^(n+1) days ago for n=1,2,...

The idea of the function is borrowed from 
[the artcile](http://blog.powerupcloud.com/2016/02/15/automate-ebs-snapshots-using-lambda-function/).

The development of the backup function was funded by [Shafer Systems LLC](http://www.shafersystems.com) 
and the code is open-sourced by their consent.
