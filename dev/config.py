#!/usr/bin/env python3
# vim modeline (put ":set modeline" into your ~/.vimrc)

import os

REPO = {
    "prd": {
        "host": "registry.example.com",
        "login": {
            "user": os.environ.get('DOCKER_USER'),
            "pass": os.environ.get('DOCKER_PASS')
        }
    },
   # "hub": {
   #     "host": "",
   # },
   # "dev": {
   #     "host": "",
   # }
}

COLOR = True

## stuff for EC2 Container Repostiory
ECR_LOGIN_MAX_AGE = 11 # hours
