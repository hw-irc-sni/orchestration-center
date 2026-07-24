# Copyright (c) 2026 Huawei Technologies Co., Ltd.
# All Rights Reserved.
#
# SPDX-License-Identifier: Apache-2.0
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

#!/usr/bin/env python3
"""Standalone self-signed certificate generation tool for the orchestration center.

Usage:
    python generate_selfsign_cert.py <cert_dir> <cert_usage>

    cert_dir:   Certificate directory path
    cert_usage: serverAuth (TLS communication) or dataSigning (data signing)

The password for the private key is entered interactively.
"""

import sys

from common.cert.certificate_generator import CertificateGenerator
from common.util.password_util import input_password_with_validation


def generate_self_signed_cert(cert_dir: str, cert_usage: str, password: str) -> bool:
    try:
        generator = CertificateGenerator(key_algorithm='RSA')
        success = generator.generate_self_signed_cert(cert_dir, cert_usage, password)

        if success:
            print(f"Successfully generated self-signed certificates in {cert_dir}")
            return True
        else:
            print(f"Failed to generate certificates")
            return False
    except Exception as e:
        print(f"Error generating certificates: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    if len(sys.argv) != 3:
        print("Usage: python generate_selfsign_cert.py <cert_dir> <cert_usage>")
        print("  cert_dir: Certificate directory path")
        print("  cert_usage: Certificate usage (serverAuth or dataSigning)")
        sys.exit(1)

    cert_dir = sys.argv[1]
    cert_usage = sys.argv[2]

    if cert_usage not in ["serverAuth", "dataSigning"]:
        print("Error: cert_usage must be 'serverAuth' or 'dataSigning'")
        sys.exit(1)

    password = input_password_with_validation("Enter private key password")

    if generate_self_signed_cert(cert_dir, cert_usage, password):
        sys.exit(0)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
