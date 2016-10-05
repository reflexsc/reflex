# Reflex

*STATUS: Pre-Release*

Reflex is a Service and Configuration management system designed for the modern world of ephemeral containers and software delivery.

Conventional configuration management systems are focused on servers, and pivot around static assets.  They also struggle with the concepts of continuous software delivery, management of secrets, and Infrastructure as Code.

Reflex is designed around the concept of services, live configuration states delivered at run-time for ephemeral deployments in continuous delivery pipelines.

Reflex is not designed to be a software delivery system, nor is it designed to be a software build system.  It is instead meant to fill in the gaps of the variety of existing systems, fixing many of the challenges faced as the industry evolves and improves.

It is designed with modern ABAC security concepts, and is meant to support secure run-time delivery of services, enhancing the solutions you already may have in place today to improve your options beyond what is possible with your current tools.

## Install Reflex Client

It requires python3, pip and virtualenv to exist on the host it is installed onto, then it loads itself into its own virtualenv.

Supported in MacOS and Linux.

For MacOS, get an updated python3:

    sudo brew install python3

	# or alternatively
    sudo brew upgrade python3

Otherwise, make sure virtualenv exists:

    sudo pip install virtualenv

Then install from the network (does not require super user privileges):

	curl -sLO https://raw.github.com/reflexsc/reflex/master/.pkg/getreflex.sh && bash ./getreflex.sh

## Install Reflex Core

Reflex Core is the back-end database and REST api.  This is not yet GA.
