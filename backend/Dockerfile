# Smart Inbox — backend (Spring Boot API + the built Angular reviewer UI).
#
# The Angular bundle is committed under src/main/resources/static (see docs/RUNNING.md), so this
# build needs no Node at all — Maven alone produces a jar that serves both /api and the UI.
#
# Built for linux/arm64 as well as amd64: the target host is an OCI Ampere A1 Always Free VM.

# ---- build ---------------------------------------------------------------------------
FROM maven:3.9-eclipse-temurin-21 AS build
WORKDIR /build

# Dependencies first, so a source-only change does not re-download the world.
COPY pom.xml .
RUN mvn -B -DskipTests dependency:go-offline || true

COPY src ./src
# Tests are skipped here on purpose: backend/src/test needs a live Oracle (docs/RUNNING.md),
# which does not exist inside a build container. They run in CI and on a developer machine.
RUN mvn -B -DskipTests package

# ---- run -----------------------------------------------------------------------------
FROM eclipse-temurin:21-jre
WORKDIR /app

# curl is for the healthcheck below; the JRE image ships neither curl nor wget.
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --system --create-home --uid 10001 inbox

COPY --from=build /build/target/smart-inbox-backend-1.0.0.jar /app/app.jar

# inbox.storage.blob-dir / render-dir default under ./data — mounted as a volume in compose.
RUN mkdir -p /app/data && chown -R inbox:inbox /app
USER inbox

# 1 GB is comfortable for four workers on the 24 GB Always Free shape. Override with JAVA_OPTS.
ENV JAVA_OPTS="-Xmx1g -XX:MaxRAMPercentage=70"
ENV BLOB_DIR=/app/data/blobs
ENV RENDER_DIR=/app/data/renders

EXPOSE 8080
HEALTHCHECK --interval=15s --timeout=5s --retries=20 --start-period=90s \
  CMD curl -fsS http://localhost:8080/actuator/health || exit 1

ENTRYPOINT ["sh", "-c", "exec java $JAVA_OPTS -jar /app/app.jar"]
