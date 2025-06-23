"""
Created on April 2025
@author: NBI
"""

import requests
from typing import List, Dict, Optional
import logging
import json
import subprocess
import sys
import os
from pathlib import Path
import datetime
import argparse
from datetime import datetime, timedelta


class PathManager:
    """Manages cross-platform file paths for the application"""
    
    def __init__(self):
        self.base_dir = os.getcwd()
        
    @property
    def paths(self) -> Dict[str, str]:
        return {
            'log_dir': self.get_path('log'),
            'properties_template': self.get_path('highlight.properties.template'),
            'properties_dir': self.get_path('properties'),
            'working_dir': self.get_path('working', 'cli_out'),
            'source_dir': self.get_path('working', 'src'),
            'results_dir': self.get_path('results'),
            'highlight_automation_jar': self.get_path('CAST Highlight Code Reader', 
                                                    'Highlight-Automation-Command',
                                                    'HighlightAutomation.jar')
        }
        
    def get_path(self, *parts: str) -> str:
        """Creates a platform-independent path"""
        return str(Path(self.base_dir).joinpath(*parts))
        
    def create_directories(self) -> None:
        """Creates all necessary directories"""
        for path_name, path in self.paths.items():
            if path_name not in ['properties_template', 'highlight_automation_jar']:
                os.makedirs(path, exist_ok=True)
                
    def get_results_file_path(self, repo_name: str, phase: str, timestamp: int) -> str:
        """Creates the results file path"""
        filename = f"HL.results.{repo_name}.{phase}-transformation.{timestamp}.zip"
        return str(Path(self.paths['results_dir']).joinpath(filename))
        
    def get_source_directory(self, repo_name: str, phase: str) -> str:
        """Creates the source directory path"""
        return str(Path(self.paths['source_dir']).joinpath(f"{repo_name}_{phase}"))


class RepoDetails:
    def __init__(self, repo_id: str, repo_url: str, repo_name: str, initial_branch: str, transformed_branch: str):
        self.repo_id = repo_id
        self.repo_url = repo_url
        self.repo_name = repo_name
        self.initial_branch = initial_branch
        self.transformed_branch = transformed_branch
        self.assessed_branch_folder = ""    
        self.transformed_branch_folder = "" 

    def __str__(self) -> str:
        return (f"RepoDetails(repo_id={self.repo_id},"
                f"repo_name = {self.repo_name}, "
                f"repo_url = {self.repo_url}, "
                f"initial_branch={self.initial_branch}, "
                f"transformed_branch={self.transformed_branch}, "
                f"assessed_folder={self.assessed_branch_folder}, "
                f"transformed_folder={self.transformed_branch_folder}")


class JsonReader:
    def __init__(self, json_data: str):
        self.data = json.loads(json_data)
        self.target_branch = self._get_target_branch()
        
    def _get_target_branch(self) -> str:
        return self.data.get("jobDetail", {}).get("targetBranch", "")

    def get_repo_details(self) -> List[RepoDetails]:
        repo_details_list = []
        repositories = self.data.get("repositories", [])
        for repo in repositories:
            if repo.get("status", "") == "SUCCESS":
                repo_name = repo.get("name", "")
                repo_location = repo.get("repositoryLocation", {})
                repo_id = repo_location.get("id", "")
                repo_url = repo_location.get("url", "")
                source_branch = repo.get("sourceBranch", "")

                repo_details = RepoDetails(
                    repo_id=repo_id,
                    repo_url=repo_url,
                    repo_name=repo_name,
                    initial_branch=source_branch,
                    transformed_branch=self.target_branch
                )
                repo_details_list.append(repo_details)

        logger.info(f"Amazon Q Code Transform JSON file information retrieved.")
        return repo_details_list


class CastApiClient:
    def __init__(self, cast_token: str, cast_url: str, timeout: int = 30):
        self.base_url = cast_url + "/WS2"
        self.timeout = timeout
        self.auth_token = cast_token
        self.headers = {
            'Authorization': f"Bearer {cast_token}",
            'Content-Type': 'application/json'
        }
        self.company_id = None

    def get_company_id(self) -> Optional[int]:
        try:
            response = requests.get(
                f"{self.base_url}/OAuthService/currentCompanyId",
                headers=self.headers,
                timeout=self.timeout
            )
            response.raise_for_status()
            
            self.company_id = response.json()
            if self.company_id is not None:
                logger.info(f'CAST Company Id retrieved = {self.company_id}')
                return self.company_id
            
            logger.error('Company ID not found in the response.')
            return None
            
        except Exception as e:
            logger.error(f"Failed to get company ID: {str(e)}")
            return None

    def create_application(self, application_name: str, repo_id: str) -> Optional[int]:
        try:
            post_application_url = f"{self.base_url}/domains/{self.company_id}/applications"
            post_application_data = json.dumps([{
                "clientRef": repo_id,
                "name": application_name,
                "status": "notArchived",
                "contributors": [],
                "domains": [{"id": self.company_id}],
                "componentIds": []
            }])

            response = requests.post(
                post_application_url, 
                headers=self.headers, 
                data=post_application_data,
                timeout=self.timeout
            )
            response.raise_for_status()

            data = response.json()
            if data.get("result"):
                application_id = data["result"][0].get("id")
                if application_id:
                    logger.info(f'Repository {application_name} created as an application in CAST Highlight: {application_id}')
                    return application_id

            logger.error(f"Application creation failed for repository {application_name}")
            return None

        except Exception as e:
            logger.error(f"Failed to create application: {str(e)}")
            return None


class CastScan:
    def __init__(self, path_manager: PathManager):
        self.path_manager = path_manager

    def define_scan_properties(self, repo_details: RepoDetails, github_token: str,
                             cast_token: str, cast_url: str, company_id: int,
                             application_id: int) -> List[Dict]:
        one_day_ago = datetime.now() - timedelta(days=1)
        one_day_ago_epoch_ms = int(one_day_ago.timestamp() * 1000)
        today_epoch_ms = int(datetime.now().timestamp() * 1000)

        source_directory_pre = self.path_manager.get_source_directory(repo_details.repo_name, 'Pre')
        source_directory_post = self.path_manager.get_source_directory(repo_details.repo_name, 'Post')

        base_properties = {
            "serverUrl": cast_url,
            "tokenAuth": cast_token,
            "companyId": company_id,
            "applicationId": application_id,
        }

        properties_updates = []
        
        # Pre-transformation properties
        pre_properties = {
            **base_properties,
            "sourceDir": source_directory_pre.replace(os.sep, "/"),
            "gitUrl": f"{repo_details.repo_url}.git",
            "gitBranch": repo_details.initial_branch,
            "zipResult": self.path_manager.get_results_file_path(
                repo_details.repo_name, 'pre', one_day_ago_epoch_ms).replace(os.sep, "/"),
            "snapshotLabel": "Pre-Transformation",
            "snapshotDatetime": one_day_ago_epoch_ms
        }

        # Post-transformation properties
        post_properties = {
            **base_properties,
            "sourceDir": source_directory_post.replace(os.sep, "/"),
            "gitUrl": f"{repo_details.repo_url}.git",
            "gitBranch": repo_details.transformed_branch,
            "zipResult": self.path_manager.get_results_file_path(
                repo_details.repo_name, 'post', today_epoch_ms).replace(os.sep, "/"),
            "snapshotLabel": "Post-Transformation",
            "snapshotDatetime": today_epoch_ms
        }

        if github_token:
            pre_properties["gitToken"] = f'Bearer {github_token}'
            post_properties["gitToken"] = f'Bearer {github_token}'

        properties_updates.extend([pre_properties, post_properties])
        return properties_updates

    def create_properties_files(self, properties_updates: Dict) -> str:
        template_path = Path(self.path_manager.paths['properties_template'])
        output_lines = []

        with template_path.open('r', encoding='utf-8') as f:
            for line in f:
                stripped = line.strip()
                if not stripped or stripped.startswith(('#', '!')):
                    output_lines.append(line)
                    continue

                if '=' in line:
                    key, _ = line.split('=', 1)
                    if key in properties_updates:
                        line = f"{key}={properties_updates[key]}\n"
                    elif key == "gitToken" and key not in properties_updates:
                        line = "# No Github Token - only public repositories\n"
                output_lines.append(line)

        output_file = Path(self.path_manager.paths['properties_dir']).joinpath(
            f"highlight.properties.{properties_updates['applicationId']}"
            f".{properties_updates['snapshotLabel']}.properties"
        )

        with output_file.open('w', encoding='utf-8') as f:
            f.writelines(output_lines)

        logger.info(f"Highlight Properties file created: {output_file}")
        return str(output_file)

    def execute_analysis(self, properties_file: str) -> int:
        logger.info("="*80)
        logger.info("Starting analysis:")
        logger.info("="*80)

        try:
            command = [
                "java", "-jar",
                self.path_manager.paths['highlight_automation_jar'],
                "--propertiesPath", properties_file
            ]

            logger.info(f"Executing command: {command}")

            with subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1
            ) as process:
                logger.info("Command output:")
                logger.info("-" * 80)

                for line in process.stdout:
                    logger.info(line.strip())

            return_code = process.returncode
            self._log_return_code(return_code)
            return return_code

        except Exception as e:
            logger.error(f"Analysis failed: {str(e)}")
            return 1

    def _log_return_code(self, return_code: int) -> None:
        return_code_messages = {
            0: "OK",
            1: "Command Line general failure",
            2: "Command Line options parse error",
            3: "Command Line techno discovery error",
            4: "Command Line analysis error",
            5: "Command Line result upload error",
            6: "Command Line source dir or output dir validation error",
            7: "Command Line result saving to zip file error",
            8: "Command Line upload from zip file error"
        }

        message = return_code_messages.get(return_code, "Unknown error")
        
        if return_code == 0:
            logger.info(f"Command completed successfully: {message}")
        else:
            logger.error(f"Command failed with return code {return_code}: {message}")


def init_parse_argument() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Automatically launches the scans of the source code of 2 git branches from git repositories transformed by AWS Transform for .Net and listed in a JSON file.')
    parser.add_argument('--json-file-path', required=True, help='Path to the JSON file')
    parser.add_argument('--github-token', required=False, help='GitHub token')
    parser.add_argument('--cast-token', required=True, help='CAST Highlight token')
    parser.add_argument('--cast-url', required=False, help='CAST Highlight URL - default is https://rpa.casthighlight.com',
                        choices=['https://rpa.casthighlight.com', 'https://cloud.casthighlight.com', 'https://app.casthighlight.com', 'https://demo.casthighlight.com'])
    return parser


if __name__ == "__main__":
    # Parse arguments
    parser = init_parse_argument()
    args = parser.parse_args()

    if args.cast_url is None :
        args.cast_url = "https://rpa.casthighlight.com"

    current_date = datetime.today()
    
    # Initialize path manager
    path_manager = PathManager()
    path_manager.create_directories()
    
    # Setup logging
    log_file = os.path.join(path_manager.paths['log_dir'], 
                           f'CASTAutomationPost_{current_date.strftime("%Y%m%d_%H%M%S")}.log')
    
    logging.basicConfig(
        level=logging.DEBUG,
        format='[%(asctime)s] - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(log_file, mode="w")
        ]
    )
    
    global logger
    logger = logging.getLogger(__name__)

    # Log parameters
    logger.info('****************** Parameters ******************')
    logger.info(f'JSON File Path = {args.json_file_path}')
    logger.info(f'Log file Path = {log_file}')
    logger.info(f'CAST Highlight URL = {args.cast_url}')
    logger.info(f'Working Directory = {path_manager.paths["working_dir"]}')
    logger.info(f'Source Directory = {path_manager.paths["source_dir"]}')
    logger.info(f'Results Directory = {path_manager.paths["results_dir"]}')
    logger.info('************************************************')

    try:
        # Initialize CAST API client
        cast_api = CastApiClient(args.cast_token, args.cast_url)
        company_id = cast_api.get_company_id()
        if not company_id:
            raise ValueError("Failed to get company ID")

        # Read JSON file
        with open(args.json_file_path, 'r') as f:
            json_data = f.read()

        # Process repositories
        json_reader = JsonReader(json_data)
        repo_details_list = json_reader.get_repo_details()
        logger.info(f'Number of Github Repositories to process: {len(repo_details_list)}')

        cast_scan = CastScan(path_manager)

        for i, repo_details in enumerate(repo_details_list, 1):
            application_id = cast_api.create_application(repo_details.repo_name, repo_details.repo_id)
            if application_id is not None:
                logger.info(f'Processing {i} {repo_details.repo_name} - {repo_details.repo_url} - {application_id}')
                
                list_update_properties = cast_scan.define_scan_properties(
                    repo_details, args.github_token, args.cast_token, args.cast_url,
                    company_id, application_id
                )
                
                # Process pre-transformation
                pre_properties_file = cast_scan.create_properties_files(list_update_properties[0])
                result = cast_scan.execute_analysis(pre_properties_file)
                if result != 0:
                    logger.error(f"Analysis failed for {repo_details.repo_name} - {repo_details.repo_url} - branch "
                               f"{repo_details.initial_branch}")
                else:
                    logger.info(f"Analysis completed successfully for {repo_details.repo_name} - {repo_details.repo_url} - branch "
                              f"{repo_details.initial_branch}")
                
                # Process post-transformation
                post_properties_file = cast_scan.create_properties_files(list_update_properties[1])
                result = cast_scan.execute_analysis(post_properties_file)
                if result != 0:
                    logger.error(f"Analysis failed for {repo_details.repo_name} - {repo_details.repo_url} - branch "
                               f"{repo_details.transformed_branch}")
                else:
                    logger.info(f"Analysis completed successfully for {repo_details.repo_name} - {repo_details.repo_url} - branch "
                              f"{repo_details.transformed_branch}")

    except Exception as e:
        logger.error(f"Script failed: {str(e)}")
        sys.exit(1)