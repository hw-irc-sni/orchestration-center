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

"""Client-side SSL context factory for outbound HTTPS calls (httpx).

Builds an ssl.SSLContext configured from the same certificate infrastructure
used by the server side (orchestrate/start.py).  The context:

  - Verifies the remote server certificate against the configured CA trust
    store (ssl_ca_certs from etc/conf/server.conf).
  - Optionally presents a client identity certificate (ssl_certfile +
    ssl_keyfile) for mutual TLS, when the remote server requires it.
  - Optionally checks the Certificate Revocation List (ssl_crl_file).
  - Applies the configured cipher suites (tls.cipher).

The result can be passed directly to httpx's ``verify`` parameter.
"""

import os
import ssl
from typing import Union

from loguru import logger

from common.config import TLS_CIPHER
from common.util.cipher_converter import CipherConverter
from common.util.cipher_util import DEFAULT_ENCODING
from common.util.conf_util import get_conf_singleton, load_cert_password
from common.util.config_util import get_conf


def create_client_ssl_context() -> Union[ssl.SSLContext, bool]:
    """Build an SSL context for outbound HTTPS calls.

    Returns an ssl.SSLContext for httpx's ``verify`` parameter, or
    ``False`` when verification should be disabled (development mode or
    missing trust store).
    """
    conf = get_conf()

    # Master switch: client_verify_server (default: false for backward compat).
    # Set to true in production with a properly configured CA trust store.
    verify_server = str(conf.get("client_verify_server", "false")).lower() == "true"
    if not verify_server:
        logger.warning(
            "Outbound TLS verification disabled (client_verify_server=false). "
            "This is insecure for production."
        )
        return False

    conf_obj = get_conf_singleton()

    try:
        ctx = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)

        # 1. CA trust store — verifies the remote server's certificate
        ca_path = conf_obj.ssl_ca_certs
        if ca_path and os.path.exists(ca_path):
            ctx.load_verify_locations(ca_path)
            logger.info(f"Client SSL: loaded CA trust store from {ca_path}")
        else:
            logger.warning(
                f"Client SSL: CA trust store not found at {ca_path}. "
                "Falling back to system default trust store."
            )

        # 2. Client identity certificate (mTLS) — optional
        cert_path = conf_obj.ssl_certfile
        key_path = conf_obj.ssl_keyfile
        if cert_path and key_path and os.path.exists(cert_path) and os.path.exists(key_path):
            pwd_bytes = load_cert_password(conf_obj.ssl_keyfile_password)
            password = pwd_bytes.decode(DEFAULT_ENCODING) if pwd_bytes else None
            try:
                ctx.load_cert_chain(
                    certfile=cert_path,
                    keyfile=key_path,
                    password=password if password else None,
                )
                logger.info("Client SSL: loaded client identity cert for mTLS")
            except Exception as e:
                logger.warning(f"Client SSL: could not load client cert chain: {e}")

        # 3. CRL checking — optional
        crl_path = conf_obj.ssl_crl_file
        if crl_path and os.path.exists(crl_path):
            ctx.load_verify_locations(crl_path)
            ctx.verify_flags |= ssl.VERIFY_CRL_CHECK_LEAF
            logger.info(f"Client SSL: enabled CRL checking from {crl_path}")

        # 4. Cipher suites
        cipher_str = conf.get(TLS_CIPHER, "")
        if cipher_str:
            openssl_ciphers = CipherConverter.convert(cipher_str)
            if openssl_ciphers:
                try:
                    ctx.set_ciphers(openssl_ciphers)
                except ssl.SSLError as e:
                    logger.warning(f"Client SSL: could not set ciphers ({e}), using defaults")

        return ctx

    except Exception as e:
        logger.error(f"Failed to build client SSL context: {e}. Falling back to no verification.")
        return False
    finally:
        # Ensure no plaintext password lingers in memory references
        if 'password' in dir():
            password = None  # noqa: F841
