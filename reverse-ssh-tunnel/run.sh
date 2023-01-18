#!/usr/bin/with-contenv bashio

bashio::log.info "Reverse tunnel initializing."

CONFIG_PATH=/data/options.json
key_file=/private.key

bashio::log.info "Debug ENV:"
env

bashio::log.info "Debug reading config"

curl -X GET -H "Authorization: Bearer ${SUPERVISOR_TOKEN}" -H "Content-Type: application/json" http://supervisor/core/api/config

bashio::log.info "Variables set, reading configuration."
bashio::log.info "Configuration debug:"
bashio::addon.config

username=$(bashio::config 'username')
host=$(bashio::config 'server.host')
port=$(bashio::config 'server.port')

bashio::log.info "Reverse tunnel configured for $username@$host"

if bashio::config.exists 'private_key'; then

  bashio::log.info "Using private key authorization"

  $(bashio::config 'private_key') > $key_file

  bashio::log.info "Initializing the ssh tunnel"
  ssh -o StrictHostKeyChecking=no \
    -o UserKnownHostsFile=/dev/null \
    -o PubkeyAuthentication=yes \
    -o PasswordAuthentication=no \
    -i $key_file \
    -R 9090:homeassistant.local:8123 \
    $username@$host

elif bashio::config.exists 'password'; then
  bashio::log.info "Using password authorization"

  password=`bashio::config 'password'`

  bashio::log.info "Initializing the ssh tunnel"
  
  sshpass -p $password ssh -o StrictHostKeyChecking=no \
    -o UserKnownHostsFile=/dev/null \
    -R 9090:homeassistant.local:8123 \
    $username@$host

else 
  bashio::log.error "Neither private key nor password provided. Exiting!"
fi 
