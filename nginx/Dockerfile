FROM nginx:1.27-alpine

COPY wasp-app /usr/share/nginx/html
COPY nginx.conf /opt/wasp/nginx.conf
COPY nginx.bootstrap.conf /opt/wasp/nginx.bootstrap.conf
COPY nginx/select-config.sh /docker-entrypoint.d/10-select-wasp-config.sh

RUN chmod 0755 /docker-entrypoint.d/10-select-wasp-config.sh
