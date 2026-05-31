FROM node:20-alpine

WORKDIR /app

# Copy package configurations and install dependencies
COPY package*.json ./
RUN npm install

# Copy frontend source files
COPY . .

EXPOSE 5173

# Bind Vite to 0.0.0.0 so port-forwarding works correctly in Docker Compose
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]
