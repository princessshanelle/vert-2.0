FROM golang:1.22-alpine AS build
WORKDIR /src
COPY go.mod ./
RUN go mod download
COPY . .
RUN go build -o /out/automation ./cmd/automation

FROM alpine:3.19
COPY --from=build /out/automation /usr/local/bin/automation
EXPOSE 8081
ENTRYPOINT ["automation"]
