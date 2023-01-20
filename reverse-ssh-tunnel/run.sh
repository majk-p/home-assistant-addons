#!/usr/bin/with-contenv bashio

bashio::log.info "Reverse tunnel initializing."

key_file=/private.key

bashio::log.info "Variables set, reading configuration."

username=$(bashio::config 'username')
host=$(bashio::config 'server.host')
port=$(bashio::config 'server.port')

bashio::log.info "Reverse tunnel configured for $username@$host"

bashio::log.info "Using private key authorization"

printf "%b\n" "$(bashio::config 'private_key')" > $key_file
chmod 600 $key_file

bashio::log.info "Initializing the ssh tunnel"

autossh -M 2000 -o StrictHostKeyChecking=no \
  -o UserKnownHostsFile=/dev/null \
  -o PubkeyAuthentication=yes \
  -o PasswordAuthentication=no \
  -i $key_file \
  -R $port:homeassistant.local:8123 \
  -N \
  $username@$host &

while true; do sleep 10; done