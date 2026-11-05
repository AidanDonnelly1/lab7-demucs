FROM xserrat/facebook-demucs:latest
LABEL MAINTAINER="aido4381@colorado.edu"

RUN pip3 install --no-cache-dir --upgrade minio redis requests jsonpickle

WORKDIR /srv
COPY worker-server.py /srv

ENTRYPOINT [ "python3", "-u", "/srv/worker-server.py" ]
