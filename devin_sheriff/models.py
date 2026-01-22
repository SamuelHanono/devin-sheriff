from datetime import datetime
from pathlib import Path
from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, JSON, ForeignKey, Index
from sqlalchemy.orm import declarative_base, relationship, sessionmaker, scoped_session

# 1. Setup Local DB Path
DB_DIR = Path.home() / ".devin-sheriff"
DB_FILE = f"sqlite:///{DB_DIR}/sheriff.db"

Base = declarative_base()

# --- MODELS ---

class Repo(Base):
    __tablename__ = "repos"
    
    id = Column(Integer, primary_key=True)
    owner = Column(String, nullable=False)
    name = Column(String, nullable=False)
    url = Column(String, unique=True, nullable=False)
    default_branch = Column(String, default="main")
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Cascade: If Repo is deleted, delete all Issues automatically
    issues = relationship("Issue", back_populates="repo", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Repo {self.owner}/{self.name}>"

class Issue(Base):
    __tablename__ = "issues"
    
    id = Column(Integer, primary_key=True)
    repo_id = Column(Integer, ForeignKey("repos.id"), nullable=False, index=True)
    number = Column(Integer, nullable=False)
    title = Column(String, nullable=False)
    body = Column(Text, nullable=True)
    state = Column(String, default="open", index=True) # open/closed
    
    # Sheriff Status: NEW, SCOPED, EXECUTING, PR_OPEN, DONE, FAILED
    status = Column(String, default="NEW", index=True) 
    
    confidence = Column(Integer, nullable=True) # 0-100
    scope_json = Column(JSON, nullable=True)
    pr_url = Column(String, nullable=True)
    last_error = Column(Text, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    repo = relationship("Repo", back_populates="issues")
    # Cascade: If Issue is deleted, delete its session history
    sessions = relationship("DevinSession", back_populates="issue", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Issue #{self.number} [{self.status}]>"

class DevinSession(Base):
    __tablename__ = "devin_sessions"
    
    id = Column(Integer, primary_key=True)
    issue_id = Column(Integer, ForeignKey("issues.id"), nullable=False)
    session_type = Column(String) # SCOPE or EXECUTE
    devin_session_id = Column(String, index=True)
    status = Column(String)
    output_json = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    issue = relationship("Issue", back_populates="sessions")

    def __repr__(self):
        return f"<Session {self.session_type} - {self.status}>"

# --- DATABASE INITIALIZATION ---

def init_db():
    """Creates tables if they don't exist and returns a session factory."""
    DB_DIR.mkdir(parents=True, exist_ok=True)
    
    # connect_args check_same_thread=False is needed for SQLite with Streamlit
    engine = create_engine(DB_FILE, connect_args={"check_same_thread": False})
    
    Base.metadata.create_all(engine)
    
    # Use sessionmaker factory
    return sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Create a global Session Factory
SessionLocal = init_db()