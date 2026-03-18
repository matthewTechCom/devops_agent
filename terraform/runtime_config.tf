resource "aws_secretsmanager_secret" "runtime_config" {
  name        = local.runtime_config_secret_name
  description = "Runtime configuration for ${local.runtime_name}."
}

resource "terraform_data" "runtime_config_secret_value" {
  triggers_replace = {
    python_executable = var.python_executable
    script_path       = "${path.module}/scripts/manage_runtime_config_secret.py"
    region            = var.aws_region
    secret_id         = aws_secretsmanager_secret.runtime_config.id
    payload_sha256    = sha256(jsonencode(local.runtime_config_secret_payload))
  }

  provisioner "local-exec" {
    command = <<-EOT
      "${var.python_executable}" "${path.module}/scripts/manage_runtime_config_secret.py" upsert \
        --region "${var.aws_region}" \
        --secret-id "${aws_secretsmanager_secret.runtime_config.id}"
    EOT

    environment = {
      RUNTIME_CONFIG_SECRET_STRING = jsonencode(local.runtime_config_secret_payload)
    }
  }
}
