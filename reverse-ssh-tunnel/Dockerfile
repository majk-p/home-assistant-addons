ARG BUILD_FROM
FROM $BUILD_FROM
USER root

# Setup base
RUN \
    set -x \ 
    && apk add --no-cache \
        libuv \
        openssh \
        autossh
COPY run.sh /
RUN chmod a+x /run.sh


# TODO move run to s6 overlay service
CMD ["/run.sh"]
