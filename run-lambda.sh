#! /bin/bash

# The script runs the Backup function one time. Use it to verify your function work
# prior to schedule it to run daily

aws lambda invoke --function-name DailyBackup --log-type Tail out.res
