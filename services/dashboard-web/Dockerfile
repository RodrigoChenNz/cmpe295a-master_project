# Layer 5 web. Dev server rather than a static build, deliberately: this is a
# master's project and the compose stack is how the team runs it, so hot reload
# is worth more here than an nginx image. `npm run build` produces the static
# bundle when that changes.
FROM node:20-alpine

WORKDIR /srv

# Manifest first, so editing a component does not reinstall node_modules.
COPY package.json ./
RUN npm install

COPY . .

EXPOSE 5173
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]
