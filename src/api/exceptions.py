"""
Jackdaw Sentry - Exception Classes
Custom exception classes for different error types
"""

from datetime import datetime, timezone
from typing import Optional, Dict, Any
from fastapi import HTTPException, status


class JackdawException(Exception):
    """Base exception class for Jackdaw Sentry"""
    
    def __init__(
        self,
        message: str,
        error_code: str = "JACKDAW_ERROR",
        status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR,
        details: Optional[Dict[str, Any]] = None
    ):
        self.message = message
        self.error_code = error_code
        self.status_code = status_code
        self.details = details or {}
        self.timestamp = datetime.now(timezone.utc)
        super().__init__(self.message)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert exception to dictionary for API response"""
        return {
            "error": "JackdawError",
            "message": self.message,
            "code": self.error_code,
            "timestamp": self.timestamp.isoformat(),
            "details": self.details
        }


class ComplianceException(JackdawException):
    """Exception for compliance-related errors"""
    
    def __init__(
        self,
        message: str,
        regulation: str = "UNKNOWN",
        error_code: str = "COMPLIANCE_ERROR",
        status_code: int = status.HTTP_422_UNPROCESSABLE_ENTITY,
        details: Optional[Dict[str, Any]] = None
    ):
        self.regulation = regulation
        super().__init__(message, error_code, status_code, details)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert compliance exception to dictionary for API response"""
        return {
            "error": "ComplianceError",
            "message": self.message,
            "code": self.error_code,
            "regulation": self.regulation,
            "timestamp": self.timestamp.isoformat(),
            "details": self.details
        }


class BlockchainException(JackdawException):
    """Exception for blockchain-related errors"""
    
    def __init__(
        self,
        message: str,
        blockchain: str = "unknown",
        error_code: str = "BLOCKCHAIN_ERROR",
        status_code: int = status.HTTP_503_SERVICE_UNAVAILABLE,
        details: Optional[Dict[str, Any]] = None
    ):
        self.blockchain = blockchain
        super().__init__(message, error_code, status_code, details)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert blockchain exception to dictionary for API response"""
        return {
            "error": "BlockchainError",
            "message": self.message,
            "code": self.error_code,
            "blockchain": self.blockchain,
            "timestamp": self.timestamp.isoformat(),
            "details": self.details
        }


class AuthenticationException(JackdawException):
    """Exception for authentication errors"""
    
    def __init__(
        self,
        message: str,
        error_code: str = "AUTHENTICATION_ERROR",
        status_code: int = status.HTTP_401_UNAUTHORIZED,
        details: Optional[Dict[str, Any]] = None
    ):
        super().__init__(message, error_code, status_code, details)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "error": "AuthenticationError",
            "message": self.message,
            "code": self.error_code,
            "timestamp": self.timestamp.isoformat(),
            "details": self.details
        }


class AuthorizationException(JackdawException):
    """Exception for authorization errors"""
    
    def __init__(
        self,
        message: str,
        required_permission: Optional[str] = None,
        error_code: str = "AUTHORIZATION_ERROR",
        status_code: int = status.HTTP_403_FORBIDDEN,
        details: Optional[Dict[str, Any]] = None
    ):
        self.required_permission = required_permission
        super().__init__(message, error_code, status_code, details)
    
    def to_dict(self) -> Dict[str, Any]:
        result = super().to_dict()
        result["error"] = "AuthorizationError"
        if self.required_permission:
            result["required_permission"] = self.required_permission
        return result


class ValidationException(JackdawException):
    """Exception for validation errors"""
    
    def __init__(
        self,
        message: str,
        field: Optional[str] = None,
        value: Optional[Any] = None,
        error_code: str = "VALIDATION_ERROR",
        status_code: int = status.HTTP_400_BAD_REQUEST,
        details: Optional[Dict[str, Any]] = None
    ):
        self.field = field
        self.value = value
        super().__init__(message, error_code, status_code, details)
    
    def to_dict(self) -> Dict[str, Any]:
        result = super().to_dict()
        result["error"] = "ValidationError"
        if self.field:
            result["field"] = self.field
        if self.value is not None:
            result["value"] = str(self.value)
        return result


class DatabaseException(JackdawException):
    """Exception for database-related errors"""
    
    def __init__(
        self,
        message: str,
        database: str = "unknown",
        operation: Optional[str] = None,
        error_code: str = "DATABASE_ERROR",
        status_code: int = status.HTTP_503_SERVICE_UNAVAILABLE,
        details: Optional[Dict[str, Any]] = None
    ):
        self.database = database
        self.operation = operation
        super().__init__(message, error_code, status_code, details)
    
    def to_dict(self) -> Dict[str, Any]:
        result = super().to_dict()
        result["error"] = "DatabaseError"
        result["database"] = self.database
        if self.operation:
            result["operation"] = self.operation
        return result


class ConfigurationException(JackdawException):
    """Exception for configuration errors"""
    
    def __init__(
        self,
        message: str,
        config_key: Optional[str] = None,
        error_code: str = "CONFIGURATION_ERROR",
        status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR,
        details: Optional[Dict[str, Any]] = None
    ):
        self.config_key = config_key
        super().__init__(message, error_code, status_code, details)
    
    def to_dict(self) -> Dict[str, Any]:
        result = super().to_dict()
        result["error"] = "ConfigurationError"
        if self.config_key:
            result["config_key"] = self.config_key
        return result


class RateLimitException(JackdawException):
    """Exception for rate limiting errors"""
    
    def __init__(
        self,
        message: str,
        limit_type: str = "unknown",
        retry_after: Optional[int] = None,
        error_code: str = "RATE_LIMIT_ERROR",
        status_code: int = status.HTTP_429_TOO_MANY_REQUESTS,
        details: Optional[Dict[str, Any]] = None
    ):
        self.limit_type = limit_type
        self.retry_after = retry_after
        super().__init__(message, error_code, status_code, details)
    
    def to_dict(self) -> Dict[str, Any]:
        result = super().to_dict()
        result["error"] = "RateLimitError"
        result["limit_type"] = self.limit_type
        if self.retry_after is not None:
            result["retry_after"] = self.retry_after
        return result


class IntelligenceException(JackdawException):
    """Exception for intelligence/threat analysis errors"""
    
    def __init__(
        self,
        message: str,
        intelligence_source: Optional[str] = None,
        error_code: str = "INTELLIGENCE_ERROR",
        status_code: int = status.HTTP_503_SERVICE_UNAVAILABLE,
        details: Optional[Dict[str, Any]] = None
    ):
        self.intelligence_source = intelligence_source
        super().__init__(message, error_code, status_code, details)
    
    def to_dict(self) -> Dict[str, Any]:
        result = super().to_dict()
        result["error"] = "IntelligenceError"
        if self.intelligence_source:
            result["intelligence_source"] = self.intelligence_source
        return result


class InvestigationException(JackdawException):
    """Exception for investigation-related errors"""
    
    def __init__(
        self,
        message: str,
        investigation_id: Optional[str] = None,
        error_code: str = "INVESTIGATION_ERROR",
        status_code: int = status.HTTP_422_UNPROCESSABLE_ENTITY,
        details: Optional[Dict[str, Any]] = None
    ):
        self.investigation_id = investigation_id
        super().__init__(message, error_code, status_code, details)
    
    def to_dict(self) -> Dict[str, Any]:
        result = super().to_dict()
        result["error"] = "InvestigationError"
        if self.investigation_id:
            result["investigation_id"] = self.investigation_id
        return result


class ReportException(JackdawException):
    """Exception for report generation errors"""
    
    def __init__(
        self,
        message: str,
        report_type: Optional[str] = None,
        error_code: str = "REPORT_ERROR",
        status_code: int = status.HTTP_422_UNPROCESSABLE_ENTITY,
        details: Optional[Dict[str, Any]] = None
    ):
        self.report_type = report_type
        super().__init__(message, error_code, status_code, details)
    
    def to_dict(self) -> Dict[str, Any]:
        result = super().to_dict()
        result["error"] = "ReportError"
        if self.report_type:
            result["report_type"] = self.report_type
        return result

