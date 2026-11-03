variable "IMAGE" {
  default = "scholar"
}

variable "TAG" {
  default = "smoke"
}

variable "REVISION" {
  default = "local"
}

group "default" {
  targets = ["web", "cron"]
}

target "base" {
  context    = "."
  dockerfile = "base/Dockerfile"
}

target "runtime" {
  context = "."
  contexts = {
    base = "target:base"
  }
  args = {
    BASE_IMAGE = "base"
  }
  labels = {
    "org.opencontainers.image.source"   = "https://github.com/RummerLab/scholar"
    "org.opencontainers.image.revision" = REVISION
    "org.opencontainers.image.version"  = TAG
  }
}

target "web" {
  inherits   = ["runtime"]
  dockerfile = "web/Dockerfile"
  tags       = ["${IMAGE}:${TAG}"]
}

target "cron" {
  inherits   = ["runtime"]
  dockerfile = "cron/Dockerfile"
  tags       = ["${IMAGE}-cron:${TAG}"]
}
