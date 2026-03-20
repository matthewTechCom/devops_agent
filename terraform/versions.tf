terraform {
  required_version = ">= 1.7.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.80.0"
    }
    external = {
      source  = "hashicorp/external"
      version = ">= 2.3.0"
    }
    random = {
      source  = "hashicorp/random"
      version = ">= 3.6.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = ">= 2.4.0"
    }
  }
}
