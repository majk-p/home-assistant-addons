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
        curl \
        openssh \
        sshpass \
        vim
COPY run.sh /
RUN chmod a+x /run.sh

CMD ["/run.sh"]
