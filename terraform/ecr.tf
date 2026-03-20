resource "aws_ecr_repository" "runtime" {
  name                 = local.runtime_repository_name
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_ecr_lifecycle_policy" "runtime" {
  repository = aws_ecr_repository.runtime.name

  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Keep the most recent 10 images"
        selection = {
          tagStatus   = "any"
          countType   = "imageCountMoreThan"
          countNumber = 10
        }
        action = {
          type = "expire"
        }
      }
    ]
  })
}

data "external" "runtime_image" {
  program = [
    var.python_executable,
    "${path.module}/scripts/get_ecr_image_digest.py",
    "get",
    "--region",
    var.aws_region,
    "--repository-name",
    aws_ecr_repository.runtime.name,
    "--image-tag",
    var.runtime_image_tag,
  ]
}
