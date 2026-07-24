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

import json
import os
from pathlib import Path
from typing import Dict, List, Optional, Any, Union
from loguru import logger


class BPMNFlowManager:
    """Manage storage and retrieval of parsed BPMN flow packages."""

    def __init__(self, storage_dir: Optional[str] = None):
        """
        Initialize BPMNFlowManager.

        Args:
            storage_dir: Storage directory path, defaults to data/bpmn_flows sibling to orchestrate directory
        """
        if storage_dir is None:
            # Get absolute path of current file, then find project root (parent of framework)
            current_file = Path(__file__).resolve()
            project_root = current_file.parent.parent  # framework directory
            self.storage_dir = project_root / "data" / "bpmn_flows"
        else:
            self.storage_dir = Path(storage_dir)

        self.storage_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"BPMNFlowManager initialized with storage directory: {self.storage_dir}")

    def _get_storage_path(self, bpmn_filename: str) -> Path:
        """
        Get storage path based on BPMN filename.

        Args:
            bpmn_filename: BPMN filename

        Returns:
            Path: Storage file path
        """
        # Remove file extension, use base filename as storage filename
        filename_without_ext = Path(bpmn_filename).stem
        storage_filename = f"{filename_without_ext}.json"
        return self.storage_dir / storage_filename

    def store_bpmn_package(self, bpmn_filename: str, processes_dict: Dict[str, str]) -> bool:
        """
        Store parsed BPMN flow data.

        Args:
            bpmn_filename: BPMN filename
            processes_dict: Process dictionary (process_id -> markdown) extracted via
                BPMNFlowParser.parse_bpmn_all_processes_dict

        Returns:
            bool: Whether storage succeeded
        """
        try:
            storage_path = self._get_storage_path(bpmn_filename)

            # Prepare storage data
            storage_data = {
                "bpmn_filename": bpmn_filename,
                "processes": processes_dict,
                "process_count": len(processes_dict),
                "process_ids": list(processes_dict.keys())
            }

            # Write JSON file
            with open(storage_path, 'w', encoding='utf-8') as f:
                json.dump(storage_data, f, ensure_ascii=False, indent=2)

            logger.info(f"Successfully stored BPMN package for '{bpmn_filename}' at {storage_path}")
            logger.info(f"Stored {len(processes_dict)} processes")
            return True

        except Exception as e:
            logger.error(f"Failed to store BPMN package for '{bpmn_filename}': {e}")
            return False

    def retrieve_by_filename(self, bpmn_filename: str) -> Optional[Dict[str, Any]]:
        """
        Retrieve BPMN flow data by BPMN filename.

        Args:
            bpmn_filename: BPMN filename

        Returns:
            Optional[Dict[str, Any]]: Retrieved data including process dictionary and other information
        """
        try:
            storage_path = self._get_storage_path(bpmn_filename)

            if not storage_path.exists():
                logger.warning(f"No BPMN package found for '{bpmn_filename}' at {storage_path}")
                return None

            # Read JSON file
            with open(storage_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            logger.info(f"Successfully retrieved BPMN package for '{bpmn_filename}'")
            logger.info(f"Retrieved {data.get('process_count', 0)} processes")
            return data

        except Exception as e:
            logger.error(f"Failed to retrieve BPMN package for '{bpmn_filename}': {e}")
            return None

    def retrieve_all(self) -> List[Dict[str, Any]]:
        """
        Retrieve all stored BPMN flow package data.

        Returns:
            List[Dict[str, Any]]: List of all stored BPMN flow package data
        """
        try:
            all_packages = []

            # Iterate through all JSON files in the storage directory
            for file_path in self.storage_dir.glob("*.json"):
                try:
                    with open(file_path, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                        all_packages.append(data)
                except Exception as e:
                    logger.warning(f"Failed to read file {file_path}: {e}")
                    continue

            logger.info(f"Retrieved {len(all_packages)} BPMN packages in total")
            return all_packages

        except Exception as e:
            logger.error(f"Failed to retrieve all BPMN packages: {e}")
            return []

    def get_all_filenames(self) -> List[str]:
        """
        Get list of all stored BPMN filenames.

        Returns:
            List[str]: BPMN filename list
        """
        try:
            filenames = []

            for file_path in self.storage_dir.glob("*.json"):
                try:
                    with open(file_path, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                        if "bpmn_filename" in data:
                            filenames.append(data["bpmn_filename"])
                except Exception as e:
                    logger.warning(f"Failed to read file {file_path}: {e}")
                    continue

            return filenames

        except Exception as e:
            logger.error(f"Failed to get all filenames: {e}")
            return []

    def delete_by_filename(self, bpmn_filename: str) -> bool:
        """
        Delete BPMN flow data for specified BPMN filename.

        Args:
            bpmn_filename: BPMN filename

        Returns:
            bool: Whether deletion succeeded
        """
        try:
            storage_path = self._get_storage_path(bpmn_filename)

            if not storage_path.exists():
                logger.warning(f"No BPMN package found for '{bpmn_filename}' to delete")
                return False

            storage_path.unlink()
            logger.info(f"Successfully deleted BPMN package for '{bpmn_filename}'")
            return True

        except Exception as e:
            logger.error(f"Failed to delete BPMN package for '{bpmn_filename}': {e}")
            return False

    def get_process_content(self, bpmn_filename: str, process_id: str) -> Optional[str]:
        """
        Get markdown content of a specific process in the specified BPMN file.

        Args:
            bpmn_filename: BPMN filename
            process_id: Process ID

        Returns:
            Optional[str]: Process markdown content, None if not exists
        """
        try:
            data = self.retrieve_by_filename(bpmn_filename)
            if not data or "processes" not in data:
                return None

            processes = data["processes"]
            return processes.get(process_id)

        except Exception as e:
            logger.error(f"Failed to get process content for '{process_id}' in '{bpmn_filename}': {e}")
            return None

    def search_processes_by_keyword(self, keyword: str) -> List[Dict[str, Any]]:
        """
        Search processes containing keyword across all stored BPMN packages.

        Args:
            keyword: Search keyword

        Returns:
            List[Dict[str, Any]]: List of matching results, each containing BPMN filename and matching processes
        """
        try:
            all_packages = self.retrieve_all()
            results = []

            for package in all_packages:
                bpmn_filename = package.get("bpmn_filename", "")
                processes = package.get("processes", {})

                matching_processes = {}
                for process_id, process_content in processes.items():
                    if keyword.lower() in process_content.lower():
                        matching_processes[process_id] = process_content

                if matching_processes:
                    results.append({
                        "bpmn_filename": bpmn_filename,
                        "matching_processes": matching_processes,
                        "match_count": len(matching_processes)
                    })

            logger.info(f"Found {len(results)} packages with processes containing keyword '{keyword}'")
            return results

        except Exception as e:
            logger.error(f"Failed to search processes by keyword '{keyword}': {e}")
            return []

    def get_storage_stats(self) -> Dict[str, Any]:
        """
        Get storage statistics.

        Returns:
            Dict[str, Any]: Storage statistics
        """
        try:
            all_packages = self.retrieve_all()
            total_packages = len(all_packages)
            total_processes = sum(package.get("process_count", 0) for package in all_packages)

            return {
                "storage_directory": str(self.storage_dir),
                "total_packages": total_packages,
                "total_processes": total_processes,
                "package_filenames": [p.get("bpmn_filename", "") for p in all_packages]
            }

        except Exception as e:
            logger.error(f"Failed to get storage stats: {e}")
            return {
                "storage_directory": str(self.storage_dir),
                "total_packages": 0,
                "total_processes": 0,
                "package_filenames": []
            }
