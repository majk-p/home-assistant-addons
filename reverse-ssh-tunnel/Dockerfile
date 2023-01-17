ARG BUILD_FROM
FROM $BUILD_FROM
USER root

# Setup base
RUN \
    set -x \ 
    && apk add --no-cache \
        bash-completion \
        libuv \
        nano \
        openssh \
        sshpass \
        vim
COPY run-reverse-tunnel.sh /
RUN chmod a+x /run-reverse-tunnel.sh

CMD ["/run-reverse-tunnel.sh"]
