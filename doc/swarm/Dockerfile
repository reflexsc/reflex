# this is for reflex engine as a server

FROM centos:7

ARG BUILD_VERSION
WORKDIR /app

# Transaction #3, #4 & #5
RUN echo "clean_requirements_on_remove=1" >> /etc/yum.conf ; \
    echo $BUILD_VERSION && \
    yum -y upgrade && \
    yum -y install epel-release && \
    yum -y install file libffi openssl python34 mariadb mariadb-libs mariadb-devel && \
    # to undo later: this is transaction #6
    yum -y install gcc gcc-c++ make curl libffi-devel python34-devel && \
    # community-mysql-devel
    curl -O https://bootstrap.pypa.io/get-pip.py && python3 get-pip.py && \
    pip3 --no-cache-dir install -U rfxengine && \
    # load mysql
    get_mysql_connector.py --with-mysql-capi=/usr -O2 && \
    # cleanup
    rm -rf ~/.pip/cache $PWD/build/ && \
    yum -y history undo 6 ; true &&\
    yum -y clean all

EXPOSE 54000

ENTRYPOINT ["/usr/bin/reflex-engine"]
