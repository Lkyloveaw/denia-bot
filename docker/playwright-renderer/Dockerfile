FROM mcr.microsoft.com/playwright:v1.60.0-noble@sha256:9bd26ad900bb5e0f4dee75839e957a89ae89c2b7ab1e76050e559790e946b948

USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-noto-cjk fonts-noto-color-emoji \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
ENV NODE_ENV=production \
    PORT=3000 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

COPY docker/playwright-renderer/package*.json ./
RUN PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 npm ci --omit=dev --no-audit --no-fund
COPY nonebot/media_parser/core/resources/HYSongYunLangHeiW-1.ttf /usr/local/share/fonts/qqbot/
RUN fc-cache -f

COPY docker/playwright-renderer/server.js ./
USER pwuser
EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD node -e "fetch('http://127.0.0.1:3000/health').then(r=>r.json()).then(h=>process.exit(h.ready===true?0:1)).catch(()=>process.exit(1))"

CMD ["node", "server.js"]
