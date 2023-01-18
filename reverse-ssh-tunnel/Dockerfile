ARG BUILD_FROM
FROM $BUILD_FROM
USER root

# Setup base
RUN \
    set -x \ 
    && apk add --no-cache \
        libuv \
        openssh \
        sshpass
COPY run.sh /
RUN chmod a+x /run.sh

CMD ["/run.sh"]
