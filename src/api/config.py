"""
Jackdaw Sentry - Configuration Settings
GDPR-compliant configuration management
"""

from typing import List
from typing import Optional

from cryptography.fernet import Fernet
from pydantic import ConfigDict
from pydantic import field_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings with GDPR compliance"""

    # =============================================================================
    # API Configuration
    # =============================================================================
    API_HOST: str = "127.0.0.1"
    API_PORT: int = 8000
    API_SECRET_KEY: str
    API_ALGORITHM: str = "HS256"
    API_ACCESS_TOKEN_EXPIRE_MINUTES: int = 30

    # CORS Settings
    ALLOWED_ORIGINS: List[str] = ["http://localhost:3000", "http://127.0.0.1:3000"]

    # =============================================================================
    # Database Configuration
    # =============================================================================

    # Neo4j Graph Database
    NEO4J_URI: str = "bolt://localhost:7687"
    NEO4J_USER: str = "neo4j"
    NEO4J_PASSWORD: str
    NEO4J_DATABASE: str = "neo4j"

    # PostgreSQL Compliance Database
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_DB: str = "jackdawsentry_compliance"
    POSTGRES_USER: str = "jackdawsentry_user"
    POSTGRES_PASSWORD: str

    # Redis Cache & Message Queue
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_PASSWORD: str
    REDIS_DB: int = 0

    # =============================================================================
    # Blockchain Configuration
    # =============================================================================

    # Bitcoin
    BITCOIN_RPC_URL: str = "https://bitcoin-rpc.publicnode.com"
    BITCOIN_RPC_USER: Optional[str] = None
    BITCOIN_RPC_PASSWORD: Optional[str] = None
    BITCOIN_NETWORK: str = "mainnet"

    # Lightning Network
    LND_RPC_URL: str = "localhost:10009"
    LND_MACAROON_PATH: Optional[str] = None
    LND_TLS_CERT_PATH: Optional[str] = None

    # Ethereum/EVM Chains
    ETHEREUM_RPC_URL: str = "wss://ethereum-rpc.publicnode.com"
    ETHEREUM_RPC_FALLBACK: str = "https://ethereum-rpc.publicnode.com"
    ETHEREUM_NETWORK: str = "mainnet"

    BSC_RPC_URL: str = "https://bsc-dataseed.binance.org"
    BSC_NETWORK: str = "mainnet"

    POLYGON_RPC_URL: str = "wss://polygon-bor-rpc.publicnode.com"
    POLYGON_RPC_FALLBACK: str = "https://polygon-bor-rpc.publicnode.com"
    POLYGON_NETWORK: str = "mainnet"

    ARBITRUM_RPC_URL: str = "wss://arbitrum-one-rpc.publicnode.com"
    ARBITRUM_RPC_FALLBACK: str = "https://arbitrum-one-rpc.publicnode.com"
    ARBITRUM_NETWORK: str = "mainnet"

    BASE_RPC_URL: str = "wss://base-rpc.publicnode.com"
    BASE_RPC_FALLBACK: str = "https://base-rpc.publicnode.com"
    BASE_NETWORK: str = "mainnet"

    AVALANCHE_RPC_URL: str = "wss://avalanche-c-chain-rpc.publicnode.com"
    AVALANCHE_RPC_FALLBACK: str = "https://avalanche-c-chain-rpc.publicnode.com"
    AVALANCHE_NETWORK: str = "mainnet"

    OPTIMISM_RPC_URL: str = "wss://optimism-rpc.publicnode.com"
    OPTIMISM_RPC_FALLBACK: str = "https://optimism-rpc.publicnode.com"
    OPTIMISM_NETWORK: str = "mainnet"

    # Solana
    SOLANA_RPC_URL: str = "wss://solana-rpc.publicnode.com"
    SOLANA_RPC_FALLBACK: str = "https://solana-rpc.publicnode.com"
    SOLANA_NETWORK: str = "mainnet"

    # SUI
    SUI_RPC_URL: str = "wss://sui-rpc.publicnode.com"
    SUI_RPC_FALLBACK: str = "https://sui-rpc.publicnode.com"
    SUI_NETWORK: str = "mainnet"

    # Starknet
    STARKNET_RPC_URL: str = "wss://starknet-rpc.publicnode.com"
    STARKNET_RPC_FALLBACK: str = "https://starknet-rpc.publicnode.com"
    STARKNET_NETWORK: str = "mainnet"

    # Injective
    INJECTIVE_RPC_URL: str = "wss://injective-rpc.publicnode.com:443/websocket"
    INJECTIVE_RPC_FALLBACK: str = "https://injective-rpc.publicnode.com:443"
    INJECTIVE_NETWORK: str = "mainnet"

    # Cosmos
    COSMOS_RPC_URL: str = "wss://cosmos-rpc.publicnode.com:443/websocket"
    COSMOS_RPC_FALLBACK: str = "https://cosmos-rpc.publicnode.com:443"
    COSMOS_NETWORK: str = "mainnet"

    # Tron
    TRON_RPC_URL: str = "https://api.trongrid.io"
    TRON_NETWORK: str = "mainnet"

    # XRPL (Ripple)
    XRPL_RPC_URL: str = "https://xrplcluster.com"
    XRPL_NETWORK: str = "mainnet"

    # Stellar
    STELLAR_RPC_URL: str = "https://horizon.stellar.org"
    STELLAR_NETWORK: str = "public"

    # Sei
    SEI_RPC_URL: str = "https://rpc.sei-apis.com"
    SEI_NETWORK: str = "mainnet"

    # Hyperliquid L1
    HYPERLIQUID_RPC_URL: str = "https://api.hyperliquid.xyz/info"
    HYPERLIQUID_NETWORK: str = "mainnet"

    # Plasma
    PLASMA_RPC_URL: str = "https://rpc.plasma.network"
    PLASMA_NETWORK: str = "mainnet"

    # =============================================================================
    # Stablecoin Configuration
    # =============================================================================

    # USD Stablecoins
    USDT_ETHEREUM: str = "0xdAC17F958D2ee523a2206206994597C13D831ec7"
    USDC_ETHEREUM: str = "0xA0b86a33E6441b6e8F9c2c2c4c4c4c4c4c4c4c4c"
    USDT_TRON: str = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"
    USDT_SOLANA: str = "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"

    # EUR Stablecoins
    EURC_ETHEREUM: str = "0x2A325e6831B0AD69618ebC6adD6f3B8c3C5d6B5f"
    EURT_ETHEREUM: str = "0x0C10bF8FbC34C309b9F6D3394b5D1F5D6E7F8A9B"

    # =============================================================================
    # Compliance & Intelligence Configuration
    # =============================================================================

    # Sanctions Lists
    SANCTIONS_UPDATE_FREQUENCY: int = 24  # hours

    # Dark Web Monitoring
    DARK_WEB_MONITORING_ENABLED: bool = True
    DARK_WEB_UPDATE_FREQUENCY: int = 12  # hours

    # ML Model Configuration
    ML_MODEL_UPDATE_FREQUENCY: int = 168  # 1 week
    ML_CONFIDENCE_THRESHOLD: float = 0.75

    # =============================================================================
    # GDPR & Data Retention Configuration
    # =============================================================================

    # Data Retention (EU AML requirement: 7 years)
    DATA_RETENTION_DAYS: int = 2555  # 7 years

    # Automatic Data Deletion
    AUTO_DELETE_EXPIRED_DATA: bool = True
    DATA_DELETION_FREQUENCY: int = 24  # hours

    # GDPR Compliance
    GDPR_CONSENT_REQUIRED: bool = True
    GDPR_DATA_SUBJECT_REQUESTS_ENABLED: bool = True

    # =============================================================================
    # Logging & Monitoring
    # =============================================================================

    LOG_LEVEL: str = "INFO"
    LOG_FILE_PATH: str = "/var/log/jackdawsentry/"
    LOG_MAX_SIZE_MB: int = 100
    LOG_BACKUP_COUNT: int = 5

    # Performance Monitoring
    METRICS_ENABLED: bool = True
    METRICS_PORT: int = 9090

    # =============================================================================
    # Development Configuration
    # =============================================================================

    DEBUG: bool = False
    TESTING: bool = False
    TRUST_PROXY_HEADERS: bool = True

    # API Rate Limiting
    RATE_LIMIT_ENABLED: bool = True
    RATE_LIMIT_REQUESTS_PER_MINUTE: int = 100

    # RPC Rate Limiting (for public blockchain RPCs)
    RPC_RATE_LIMIT_PER_MINUTE: int = 60
    RPC_REQUEST_TIMEOUT_SECONDS: int = 30

    # Optional block explorer API keys (for indexed tx history)
    ETHERSCAN_API_KEY: Optional[str] = None
    BLOCKSTREAM_API_URL: str = "https://blockstream.info/api"

    # Cache Configuration
    CACHE_TTL_SECONDS: int = 300
    CACHE_MAX_SIZE: int = 1000

    # =============================================================================
    # Security Configuration
    # =============================================================================

    # Encryption
    ENCRYPTION_KEY: str
    ENCRYPTION_ALGORITHM: str = "AES-256-GCM"

    # JWT Configuration
    JWT_SECRET_KEY: str
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = 1440  # 24 hours

    # =============================================================================
    # Validators
    # =============================================================================

    model_config = ConfigDict(env_file=".env", case_sensitive=True, extra="ignore")

    @field_validator("ENCRYPTION_KEY")
    @classmethod
    def validate_encryption_key(cls, v):
        """Validate encryption key is non-empty and at least 32 characters"""
        if not v or not v.strip():
            raise ValueError("ENCRYPTION_KEY environment variable is required")
        if len(v) < 32:
            raise ValueError("Encryption key must be at least 32 characters")
        return v

    @field_validator("API_SECRET_KEY")
    @classmethod
    def validate_api_secret_key(cls, v):
        """Validate API secret key is provided and strong enough"""
        if not v or not v.strip():
            raise ValueError("API_SECRET_KEY environment variable is required")
        if len(v) < 32:
            raise ValueError("API secret key must be at least 32 characters")
        return v

    @field_validator("DATA_RETENTION_DAYS")
    @classmethod
    def validate_retention_period(cls, v):
        """Ensure retention period meets EU AML requirements"""
        if v < 2555:  # 7 years
            raise ValueError(
                "Data retention period must be at least 2555 days (7 years) for EU AML compliance"
            )
        return v

    @field_validator("NEO4J_PASSWORD", mode="before")
    @classmethod
    def validate_required_neo4j_password(cls, v):
        """Validate Neo4j password is provided"""
        if not v or v.strip() == "":
            raise ValueError("NEO4J_PASSWORD environment variable is required")
        return v

    @field_validator("POSTGRES_PASSWORD", mode="before")
    @classmethod
    def validate_required_postgres_password(cls, v):
        """Validate PostgreSQL password is provided"""
        if not v or v.strip() == "":
            raise ValueError("POSTGRES_PASSWORD environment variable is required")
        return v

    @field_validator("REDIS_PASSWORD", mode="before")
    @classmethod
    def validate_required_redis_password(cls, v):
        """Validate Redis password is provided"""
        if not v or v.strip() == "":
            raise ValueError("REDIS_PASSWORD environment variable is required")
        return v

    @field_validator("JWT_SECRET_KEY", mode="before")
    @classmethod
    def validate_required_jwt_secret_key(cls, v):
        """Validate JWT secret key is provided"""
        if not v or v.strip() == "":
            raise ValueError("JWT_SECRET_KEY environment variable is required")
        return v


# Create global settings instance
settings = Settings()


# =============================================================================
# Encryption Helper
# =============================================================================


def get_encryption_key() -> bytes:
    """Get raw encryption key bytes for GDPR compliance.

    If ENCRYPTION_KEY looks like a hex string (even-length, all hex chars),
    it is decoded via bytes.fromhex(); otherwise it is treated as a UTF-8
    passphrase and encoded to bytes.  The raw material is then run through
    HKDF-SHA256 to derive exactly 32 bytes.
    """
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF

    raw = settings.ENCRYPTION_KEY
    # Detect hex-encoded key vs. plain passphrase
    try:
        if len(raw) % 2 == 0 and all(c in "0123456789abcdefABCDEF" for c in raw):
            key_material = bytes.fromhex(raw)
        else:
            key_material = raw.encode("utf-8")
    except (ValueError, AttributeError):
        key_material = raw.encode("utf-8")

    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b"jackdawsentry-gdpr-kdf-salt",
        info=b"jackdawsentry-encryption-key",
    )
    return hkdf.derive(key_material)


def get_fernet() -> Fernet:
    """Get Fernet encryption instance.
    Derives a valid Fernet key (url-safe base64 of 32 bytes) from ENCRYPTION_KEY."""
    import base64

    raw = get_encryption_key()
    fernet_key = base64.urlsafe_b64encode(raw)
    return Fernet(fernet_key)


# =============================================================================
# GDPR Compliance Helpers
# =============================================================================


def is_gdpr_enabled() -> bool:
    """Check if GDPR compliance is enabled"""
    return settings.GDPR_CONSENT_REQUIRED


def get_data_retention_days() -> int:
    """Get data retention period in days"""
    return settings.DATA_RETENTION_DAYS


def is_auto_deletion_enabled() -> bool:
    """Check if automatic data deletion is enabled"""
    return settings.AUTO_DELETE_EXPIRED_DATA


# =============================================================================
# Blockchain Configuration Helpers
# =============================================================================


def get_supported_blockchains() -> List[str]:
    """Get list of supported blockchains"""
    return [
        "bitcoin",
        "ethereum",
        "bsc",
        "polygon",
        "arbitrum",
        "base",
        "avalanche",
        "optimism",
        "solana",
        "sui",
        "starknet",
        "injective",
        "cosmos",
        "tron",
        "xrpl",
        "stellar",
        "sei",
        "hyperliquid",
        "plasma",
    ]


def get_supported_stablecoins() -> List[str]:
    """Get list of supported stablecoins"""
    return [
        "USDT",
        "USDC",
        "RLUSD",
        "USDe",
        "USDS",
        "USD1",
        "BUSD",
        "A7A5",
        "EURC",
        "EURT",
        "BRZ",
        "EURS",
    ]


def get_blockchain_config(blockchain: str) -> dict:
    """Get configuration for specific blockchain"""
    configs = {
        "bitcoin": {
            "rpc_url": settings.BITCOIN_RPC_URL,
            "network": settings.BITCOIN_NETWORK,
            "user": settings.BITCOIN_RPC_USER,
            "password": settings.BITCOIN_RPC_PASSWORD,
            "family": "bitcoin",
        },
        "ethereum": {
            "rpc_url": settings.ETHEREUM_RPC_URL,
            "fallback_url": settings.ETHEREUM_RPC_FALLBACK,
            "network": settings.ETHEREUM_NETWORK,
            "family": "evm",
        },
        "bsc": {
            "rpc_url": settings.BSC_RPC_URL,
            "network": settings.BSC_NETWORK,
            "family": "evm",
        },
        "polygon": {
            "rpc_url": settings.POLYGON_RPC_URL,
            "fallback_url": settings.POLYGON_RPC_FALLBACK,
            "network": settings.POLYGON_NETWORK,
            "family": "evm",
        },
        "arbitrum": {
            "rpc_url": settings.ARBITRUM_RPC_URL,
            "fallback_url": settings.ARBITRUM_RPC_FALLBACK,
            "network": settings.ARBITRUM_NETWORK,
            "family": "evm",
        },
        "base": {
            "rpc_url": settings.BASE_RPC_URL,
            "fallback_url": settings.BASE_RPC_FALLBACK,
            "network": settings.BASE_NETWORK,
            "family": "evm",
        },
        "avalanche": {
            "rpc_url": settings.AVALANCHE_RPC_URL,
            "fallback_url": settings.AVALANCHE_RPC_FALLBACK,
            "network": settings.AVALANCHE_NETWORK,
            "family": "evm",
        },
        "optimism": {
            "rpc_url": settings.OPTIMISM_RPC_URL,
            "fallback_url": settings.OPTIMISM_RPC_FALLBACK,
            "network": settings.OPTIMISM_NETWORK,
            "family": "evm",
        },
        "solana": {
            "rpc_url": settings.SOLANA_RPC_URL,
            "fallback_url": settings.SOLANA_RPC_FALLBACK,
            "network": settings.SOLANA_NETWORK,
            "family": "solana",
        },
        "sui": {
            "rpc_url": settings.SUI_RPC_URL,
            "fallback_url": settings.SUI_RPC_FALLBACK,
            "network": settings.SUI_NETWORK,
            "family": "sui",
        },
        "starknet": {
            "rpc_url": settings.STARKNET_RPC_URL,
            "fallback_url": settings.STARKNET_RPC_FALLBACK,
            "network": settings.STARKNET_NETWORK,
            "family": "starknet",
        },
        "injective": {
            "rpc_url": settings.INJECTIVE_RPC_URL,
            "fallback_url": settings.INJECTIVE_RPC_FALLBACK,
            "network": settings.INJECTIVE_NETWORK,
            "family": "cosmos",
        },
        "cosmos": {
            "rpc_url": settings.COSMOS_RPC_URL,
            "fallback_url": settings.COSMOS_RPC_FALLBACK,
            "network": settings.COSMOS_NETWORK,
            "family": "cosmos",
        },
        "tron": {
            "rpc_url": settings.TRON_RPC_URL,
            "network": settings.TRON_NETWORK,
            "family": "tron",
        },
        "xrpl": {
            "rpc_url": settings.XRPL_RPC_URL,
            "network": settings.XRPL_NETWORK,
            "family": "xrpl",
        },
        "stellar": {
            "rpc_url": settings.STELLAR_RPC_URL,
            "network": settings.STELLAR_NETWORK,
            "family": "stellar",
        },
        "sei": {
            "rpc_url": settings.SEI_RPC_URL,
            "network": settings.SEI_NETWORK,
            "family": "evm",
        },
        "hyperliquid": {
            "rpc_url": settings.HYPERLIQUID_RPC_URL,
            "network": settings.HYPERLIQUID_NETWORK,
            "family": "hyperliquid",
        },
        "plasma": {
            "rpc_url": settings.PLASMA_RPC_URL,
            "network": settings.PLASMA_NETWORK,
            "family": "evm",
        },
    }
    return configs.get(blockchain, {})


# =============================================================================
# Environment Detection
# =============================================================================


def is_development() -> bool:
    """Check if running in development mode"""
    return settings.DEBUG


def is_production() -> bool:
    """Check if running in production mode"""
    return not settings.DEBUG


def is_testing() -> bool:
    """Check if running in testing mode"""
    return settings.TESTING
