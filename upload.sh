#! /bin/bash

# This script creates a package (basically a simple ZIP) and uploads it to AWS
# It assumes that you have 'ec2backup-role' already defined in your account
# The role must include the predefined policy 'AWSLambdaBasicExecutionRole' plus
# ec2:Describe*, ec2:CreateImage, ec2:DeregisterImage and ec2:DeleteSnapshot

rm -f backup-func.zip
zip -9 backup-func.zip backup_func.py

aws lambda delete-function --function-name DailyBackup

sleep 5

aws lambda create-function --function-name DailyBackup --runtime python2.7 --timeout 30 \
	--role arn:aws:iam::123456789012:role/ec2backup-role --handler backup_func.lambda_handler \
	--zip-file fileb://backup-func.zip
