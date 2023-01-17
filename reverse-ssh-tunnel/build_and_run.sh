docker build --build-arg BUILD_FROM=ghcr.io/home-assistant/amd64-base:3.16 -t local/reverse-ssh . 
docker run --rm -v /tmp/test-data:/data local/reverse-ssh
