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

import random
import string


class PasswordGenerator:
    """Password generator for producing random passwords meeting complexity requirements."""

    DIGITS = string.digits
    UPPER = string.ascii_uppercase
    LOWER = string.ascii_lowercase
    SPECIAL = "`~!@#$%^&*()-_=+|[{}];:'\",<.>/? "

    def __init__(self):
        self.all_chars = self.DIGITS + self.UPPER + self.LOWER + self.SPECIAL

    def generate_password(self, length: int = 16) -> str:
        """
        Generate a random password meeting complexity requirements.
        :param length: Password length, default 16.
        :return: Random password.
        """
        if length < 8:
            raise ValueError("Password length must be at least 8")

        password = []
        char_types = [self.DIGITS, self.UPPER, self.LOWER, self.SPECIAL]
        random.shuffle(char_types)

        for i in range(length):
            if i < 4:
                password.append(self._generate_random_char(char_types[i]))
            else:
                password.append(self._generate_random_char(self.all_chars))

        random.shuffle(password)
        return ''.join(password)

    def _generate_random_char(self, chars: str) -> str:
        """
        Generate a random character from the given character set.
        :param chars: Character set.
        :return: Random character.
        """
        return random.choice(chars)
