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

import getpass
import re


def validate_password_complexity(password: str) -> tuple[bool, str]:
    min_length = 8
    digit_pattern = re.compile(r'[0-9]')
    upper_pattern = re.compile(r'[A-Z]')
    lower_pattern = re.compile(r'[a-z]')
    special_pattern = re.compile(r'[`~!@#$%^&*()_=+|\[\{\}\];:\'",<.>/? -]')

    if len(password) == 0:
        return False, "Password is empty"

    if len(password) < min_length:
        return False, f"At least {min_length} characters"

    char_types = sum(bool(re.search(pattern, password)) for pattern in
                     [digit_pattern, upper_pattern, lower_pattern, special_pattern])
    if char_types < 2:
        return False, "Include at least two character types"

    return True, ""


def input_password_with_validation(prompt: str) -> str:
    while True:
        password = getpass.getpass(f"{prompt}: ")
        result, error = validate_password_complexity(password)
        if not result:
            print(f"Private key password complexity is low ({error}), continue using this password? (y/n): ", end='')
            confirm = input().strip().lower()
            if confirm == 'y':
                return password
            continue
        return password
