FROM rust:1-bookworm AS builder
WORKDIR /app
COPY Cargo.toml ./
COPY src ./src
ENV CARGO_BUILD_JOBS=2
RUN cargo test --release && cargo build --release

FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=builder /app/target/release/nullcode /usr/local/bin/nullcode
USER 1001:1001
ENTRYPOINT ["/usr/local/bin/nullcode"]
