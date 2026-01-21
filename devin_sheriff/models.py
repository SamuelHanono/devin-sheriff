from datetime import datetime
from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, JSON, ForeignKey
from sqlalchemy.orm import declarative_base, relationship, sessionmaker
from pathlib import Path

# Setup local DB path
DB_DIR = Path.home() / ".devin-sheriff"
DB_FILE = f"sqlite:///{DB_DIR}/sheriff.db"

Base = declarative_base()

class Repo(Base):
    __tablename__ = "repos"
    id = Column(Integer, primary_key=True)
    owner = Column(String, nullable=False)
    name = Column(String, nullable=False)
    url = Column(String, unique=True, nullable=False)
    default_branch = Column(String, default="main")
    created_at = Column(DateTime, default=datetime.utcnow)
    
    issues = relationship("Issue", back_populates="repo")

class Issue(Base):
    __tablename__ = "issues"
    id = Column(Integer, primary_key=True)
    repo_id = Column(Integer, ForeignKey("repos.id"))
    number = Column(Integer, nullable=False)
    title = Column(String, nullable=False)
    body = Column(Text, nullable=True)
    state = Column(String, default="open") # open/closed
    
    # Sheriff Status: NEW, SCOPING, SCOPED, EXECUTING, PR_OPEN, DONE, FAILED
    status = Column(String, default="NEW") 
    confidence = Column(Integer, nullable=True) # 0-100
    scope_json = Column(JSON, nullable=True)
    pr_url = Column(String, nullable=True)
    last_error = Column(Text, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    repo = relationship("Repo", back_populates="issues")

class DevinSession(Base):
    __tablename__ = "devin_sessions"
    id = Column(Integer, primary_key=True)
    issue_id = Column(Integer, ForeignKey("issues.id"))
    session_type = Column(String) # SCOPE or EXECUTE
    devin_session_id = Column(String)
    status = Column(String)
    output_json = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

def init_db():
    """Creates tables if they don't exist."""
    DB_DIR.mkdir(parents=True, exist_ok=True)
    engine = create_engine(DB_FILE)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)

# Global Session Factory
SessionLocal = init_db()