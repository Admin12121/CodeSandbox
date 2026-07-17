ARG BASE_IMAGE=docker:dind
FROM ${BASE_IMAGE}

# LXCFS virtualizes selected procfs/sysfs files for nested sandbox containers,
# so tools such as htop, btop and free report the container's cgroup limits
# instead of the Docker host's physical topology.
RUN apk add --no-cache fuse3 lxcfs

COPY codesandbox-dind-entrypoint.sh /usr/local/bin/codesandbox-dind-entrypoint.sh
RUN sed -i 's/\r$//' /usr/local/bin/codesandbox-dind-entrypoint.sh \
    && chmod 0755 /usr/local/bin/codesandbox-dind-entrypoint.sh

ENTRYPOINT ["/usr/local/bin/codesandbox-dind-entrypoint.sh"]
