node modules for interfacing with reflex as a client

install:

    npm install rfxcmd

(or add to packages.json)

Pulling in configurations is intentionally done synchronously, which means you cannot do it at a global level.  Consider the following example, where the reflex environment settings are either in process.env or in /run/secrets (see more in [configuring environment](#configuringenvironment] below):

    const reflex = require('rfxcmd')

    async function main () {
      const config = await reflex.load()
      console.log(config)
    }
    main()

# configuring environment

You need a reflex engine server to connect against.  You can setup the [test drive](https://reflex.cold.org/docs/install/#test-drive) for a quick test run.  For your node process or container you will want to setup at least three variables (environment or as docker secrets):

    REFLEX_URL=https://reflex-server:port/base/api
    REFLEX_APIKEY=your.reflexapikey
    REFLEX_CONFIG=name-of-config-item

If configuring as docker secrets, each is expected to be mapped individually by name, such as in a docker-compose yaml file:

    version: "3.3"
    services:
      mysvc:
        # other stuff
        environment:
          - REFLEX_CONFIG=your-config-item-p1
        secrets:
          - source: reflex-url
            target: REFLEX_URL
          - source: reflex-your-apikey-v1
            target: REFLEX_APIKEY

    secrets:
      reflex-url:
        external: true
      reflex-your-apikey-v1:
        external: true

And the secrets are setup via CLI:

    docker secrets create reflex-url https://your-domain:port/path/to/api
    docker secrets create reflex-your-apikey-v1 your.apikeydatagoeshere

(note: you can version the secret like above to allow for future rotation as changes are made)

# todo

* setup as a supported option in to be called via the conventional npm-config project
* allow for variant shims besides reflex, for `.load()`, such as AWS secrets
* verify and document functionality for lambda's and [openfaas](https://www.openfaas.com/)

