services:
  signal-cli:
    # Pin to digest for supply chain safety. Update periodically after review.
    # To update: docker pull bbernhard/signal-cli-rest-api:latest && docker inspect --format='{{index .RepoDigests 0}}'
    image: bbernhard/signal-cli-rest-api@sha256:c51c117d030d051f0cf2ef17f2cb135f1301810f9526a224be67daded8f272d6
    restart: unless-stopped
    environment:
      - MODE=normal
    volumes:
      - signal-data:/home/.local/share/signal-cli
    networks:
      - clean-data-net
    healthcheck:
      test: ["CMD-SHELL", "wget -q --spider http://localhost:8080/v1/about || exit 1"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 15s
    cap_drop:
      - ALL
    cap_add:
      - CHOWN
      - SETUID
      - SETGID
    security_opt:
      - no-new-privileges:true
    mem_limit: 512m
    cpus: "1.0"
    pids_limit: 50

  clean-data:
    build: .
    restart: unless-stopped
    read_only: true
    depends_on:
      signal-cli:
        condition: service_healthy
    env_file:
      - .env
    environment:
      - SIGNAL_CLI_URL=http://signal-cli:8080
      - TEMP_DIR=/tmp/clean-data
    tmpfs:
      - /tmp/clean-data:size=512M,mode=0700,uid=1000,gid=1000
      - /tmp:size=64M
    networks:
      - clean-data-net
    healthcheck:
      test: ["CMD", "python3", "-c", "import os; os.kill(1, 0)"]
      interval: 30s
      timeout: 5s
      retries: 3
    cap_drop:
      - ALL
    security_opt:
      - no-new-privileges:true
    mem_limit: 2g
    cpus: "2.0"
    pids_limit: 100

volumes:
  signal-data:

networks:
  clean-data-net:
    driver: bridge
