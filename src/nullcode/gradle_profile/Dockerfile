FROM gradle:8.14.3-jdk21
USER root
ENV GRADLE_USER_HOME=/opt/nullcode-cache
WORKDIR /opt/bootstrap
COPY build.gradle settings.gradle gradle.properties ./
COPY CacheSmokeTest.java ./src/test/java/CacheSmokeTest.java
RUN gradle --no-daemon --max-workers=1 cacheDependencies \
    && gradle --offline --no-daemon --max-workers=1 test \
    && chmod -R a+rX /opt/nullcode-cache
USER 1001:1001
WORKDIR /work
ENTRYPOINT ["/bin/sleep"]
CMD ["900"]
