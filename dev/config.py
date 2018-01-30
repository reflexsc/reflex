#!/usr/bin/env python3
# vim modeline (put ":set modeline" into your ~/.vimrc)

ECR = {
    "prod": {
        "host": "",
        "last": "~/.ecr_last.prod",
        "aws-region": "",
        "aws-user": "",
    },
    "hub": {
        "host": "",
        "last": "",
        "aws-region": "",
        "aws-user": "",
    },
    "dev": {
        "host": "",
        "last": "~/.ecr_last.dev",
        "aws-region": "",
        "aws-user": "",
    }
}

COLOR = True

## stuff for EC2 Container Repostiory
ECR_LOGIN_MAX_AGE = 11 # hours
