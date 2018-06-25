# Installing Reflex Engine in Docker Swarm

1. Install MariaDB somewhere you can access. Save the password for later (you can use an RDS instance if you are in AWS).

2. Build the image for your-repo:

    docker build -t your-repo/reflex-engine:prd .

3. Push the image to your-repo:

    docker push your-repo/reflex-engine:prd

4. Create the configuration and Docker Secret:

    * Reference defaults at [REFLEX_ENGINE_CONFIG.json](REFLEX_ENGINE_CONFIG.json) (you only need to define what has changed)
    * At a minimum, define:
        * Your db address and password
        * A crypto key, which you can generate with:

    dd if=/dev/urandom bs=64 count=1 | base64 -w0

          Note on crypto keys: You can rotate them by adding more (just enumerate) and change the default.  Old keys will still decrypt old data, new data is encrypted with the default key.

5. Deploy the configuration secret:

	docker secret create REFLEX_ENGINE_CONFIG < REFLEX_ENGINE_CONFIG.json

5. Adjust and deploy from the [docker-compose.yml](docker-compose-yml):

	docker stack deploy -c docker-compose.yml

6. After reflex engine is running, it will generate a master API key and print it to your logs.  You use this to create new configurations.  Change it.
