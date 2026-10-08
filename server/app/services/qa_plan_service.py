"""
QA Plan Storage Service
Manages caching and versioning of QA plans linked to API versions.
"""
import os
import json
from datetime import datetime
from typing import Optional, Dict
from app.config import DATABASE_DIR


class QAPlanService:
    """
    Service for storing, retrieving, and managing QA plans.
    QA plans are cached to avoid regenerating on every page load.
    """

    def __init__(self, base_path=DATABASE_DIR):
        self.base_path = base_path
        os.makedirs(base_path, exist_ok=True)

    def _get_qa_plan_dir(self, repository: str, api_name: str):
        """Get directory path for storing QA plans"""
        qa_dir = os.path.join(self.base_path, "qa_plans", repository)
        os.makedirs(qa_dir, exist_ok=True)
        return qa_dir

    def _get_qa_plan_file(self, repository: str, api_name: str, version: int):
        """Get file path for a specific QA plan"""
        qa_dir = self._get_qa_plan_dir(repository, api_name)
        return os.path.join(qa_dir, f"{api_name}_v{version}.json")

    def save_qa_plan(
        self,
        repository: str,
        api_name: str,
        version: int,
        qa_plan: Dict,
        api_doc_hash: Optional[str] = None
    ) -> bool:
        """
        Save a QA plan for an API version.
        
        Args:
            repository: Repository name
            api_name: API name
            version: API version number
            qa_plan: QA plan dictionary
            api_doc_hash: Optional hash of the API documentation (for change detection)
            
        Returns:
            True if saved successfully, False otherwise
        """
        try:
            file_path = self._get_qa_plan_file(repository, api_name, version)
            
            # Add metadata
            qa_plan_with_meta = {
                "api_name": api_name,
                "api_version": version,
                "repository": repository,
                "generated_at": datetime.utcnow().isoformat(),
                "api_doc_hash": api_doc_hash,
                "plan": qa_plan
            }
            
            with open(file_path, "w") as f:
                json.dump(qa_plan_with_meta, f, indent=2)
            
            print(f"✅ QA Plan saved: {repository}/{api_name} v{version}")
            return True
            
        except Exception as e:
            print(f"❌ Error saving QA plan: {e}")
            return False

    def get_qa_plan(self, repository: str, api_name: str, version: int) -> Optional[Dict]:
        """
        Retrieve a cached QA plan for an API version.
        
        Args:
            repository: Repository name
            api_name: API name
            version: API version number
            
        Returns:
            QA plan dictionary with metadata, or None if not found
        """
        try:
            file_path = self._get_qa_plan_file(repository, api_name, version)
            
            if not os.path.exists(file_path):
                return None
            
            with open(file_path, "r") as f:
                qa_plan_data = json.load(f)
            
            return qa_plan_data
            
        except Exception as e:
            print(f"⚠️ Error retrieving QA plan: {e}")
            return None

    def plan_exists(self, repository: str, api_name: str, version: int) -> bool:
        """Check if a QA plan exists for an API version"""
        file_path = self._get_qa_plan_file(repository, api_name, version)
        return os.path.exists(file_path)

    def get_all_plans_for_api(self, repository: str, api_name: str) -> Dict[int, Dict]:
        """
        Get all QA plans for an API across all versions.
        
        Returns:
            Dictionary mapping version number to QA plan metadata
        """
        try:
            qa_dir = self._get_qa_plan_dir(repository, api_name)
            plans = {}
            
            for filename in os.listdir(qa_dir):
                if not filename.startswith(f"{api_name}_v"):
                    continue
                
                try:
                    version_str = filename.replace(f"{api_name}_v", "").replace(".json", "")
                    version = int(version_str)
                    
                    file_path = os.path.join(qa_dir, filename)
                    with open(file_path, "r") as f:
                        plan_data = json.load(f)
                    
                    plans[version] = {
                        "generated_at": plan_data.get("generated_at"),
                        "is_template": plan_data.get("plan", {}).get("is_template", False)
                    }
                except (ValueError, json.JSONDecodeError):
                    continue
            
            return plans
            
        except Exception as e:
            print(f"⚠️ Error retrieving all QA plans: {e}")
            return {}

    def delete_qa_plan(self, repository: str, api_name: str, version: int) -> bool:
        """Delete a cached QA plan"""
        try:
            file_path = self._get_qa_plan_file(repository, api_name, version)
            if os.path.exists(file_path):
                os.remove(file_path)
                print(f"✅ QA Plan deleted: {repository}/{api_name} v{version}")
                return True
            return False
        except Exception as e:
            print(f"❌ Error deleting QA plan: {e}")
            return False

    def get_generation_info(self, repository: str, api_name: str, version: int) -> Optional[Dict]:
        """
        Get metadata about when a QA plan was generated.
        
        Returns:
            Dictionary with generated_at and is_template info
        """
        qa_plan = self.get_qa_plan(repository, api_name, version)
        if qa_plan:
            return {
                "generated_at": qa_plan.get("generated_at"),
                "is_template": qa_plan.get("plan", {}).get("is_template", False)
            }
        return None
