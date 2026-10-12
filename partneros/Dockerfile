FROM node:24-slim
WORKDIR /app
RUN npm install --global pnpm@11.19.0
COPY package.json pnpm-lock.yaml ./
RUN pnpm install --prod --frozen-lockfile --ignore-scripts
COPY src ./src
COPY migrations ./migrations
COPY public ./public
ENV NODE_ENV=production
USER node
EXPOSE 3000
CMD ["node", "src/server.ts"]
