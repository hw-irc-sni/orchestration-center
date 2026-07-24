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

from database.utils.db_connection import create_connection
from database.utils.query_execution import execute_query
from loguru import logger


def create_tables():
    create_psop_sql = """
                       CREATE TABLE IF NOT EXISTS psop
                       (
                            id           VARCHAR(1024) PRIMARY KEY,
                            name         VARCHAR(1024) NOT NULL,
                            description  VARCHAR(1024),
                            psop_content    TEXT
                       )
                       """
    create_execution_record_sql = """
                       CREATE TABLE IF NOT EXISTS execution_records
                       (
                            execution_id    VARCHAR(64) PRIMARY KEY,
                            psop_id         VARCHAR(64) NOT NULL,
                            psop_name       VARCHAR(1024),
                            started_at      TIMESTAMP,
                            completed_at    TIMESTAMP,
                            status          VARCHAR(32),
                            step_count      INTEGER DEFAULT 0,
                            record_content  TEXT
                       )
                       """
    create_users_sql = """
                       CREATE TABLE IF NOT EXISTS users
                       (
                            id            SERIAL PRIMARY KEY,
                            username      VARCHAR(64) UNIQUE NOT NULL,
                            password_hash VARCHAR(128) NOT NULL,
                            salt          VARCHAR(64) NOT NULL,
                            role          VARCHAR(16) DEFAULT 'user',
                            created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                       )
                       """
    conn = create_connection()
    if conn is None:
        raise RuntimeError("Unable to create database connection; tables not created")
    try:
        _, err1 = execute_query(conn, create_psop_sql)
        if err1:
            raise RuntimeError(f"Failed to create psop table: {err1}")
        _, err2 = execute_query(conn, create_execution_record_sql)
        if err2:
            raise RuntimeError(f"Failed to create execution_records table: {err2}")
        _, err3 = execute_query(conn, create_users_sql)
        if err3:
            raise RuntimeError(f"Failed to create users table: {err3}")
        logger.info("Database tables verified/created: psop, execution_records, users")
    finally:
        conn.close()
