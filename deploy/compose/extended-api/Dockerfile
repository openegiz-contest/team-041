# OpenTwins extended API, built locally from a pinned upstream commit.
# The source comes as one tarball over HTTPS with retries instead of a git
# fetch: BuildKit's git source has no retry, and a slow or unstable link to
# GitHub made `git fetch` hang for minutes and fail ("early EOF").
FROM docker.io/curlimages/curl:8.10.1 AS source
ARG REF=b49663dea326854a2323423eaeab7e7f54d325a7
WORKDIR /tmp
RUN curl -fsSL --retry 8 --retry-all-errors --retry-delay 5 --connect-timeout 20 --max-time 300 \
      -o src.tgz "https://codeload.github.com/ertis-research/extended-api-for-eclipse-ditto/tar.gz/${REF}" \
 && mkdir src && tar -xzf src.tgz -C src --strip-components=1

FROM docker.io/library/node:20-slim
WORKDIR /usr/src/app
COPY --from=source /tmp/src/ ./
RUN yarn install --frozen-lockfile --network-timeout 600000
EXPOSE 8080
CMD ["yarn", "start"]
