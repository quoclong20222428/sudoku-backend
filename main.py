from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, Boolean
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
from datetime import datetime
import json

app = FastAPI()

# CORS setup
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# SQLite database setup
DATABASE_URL = "sqlite:///./sudoku.db"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Database model
class GameState(Base):
    __tablename__ = "game_states"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String, index=True)
    board = Column(Text)
    initial_puzzle = Column(Text)
    time_played = Column(Integer)
    level = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    is_hidden = Column(Boolean, default=False)  # Thêm trường is_hidden

Base.metadata.create_all(bind=engine)

# Pydantic models
class GameStateCreate(BaseModel):
    user_id: str
    board: list
    initial_puzzle: list
    time_played: int
    level: str
    is_hidden: bool = True  # Mặc định là ẩn cho lần lưu đầu tiên

class GameStateUpdate(BaseModel):
    board: list
    time_played: int
    is_hidden: bool = False  # Khi cập nhật, có thể thay đổi trạng thái ẩn/hiển thị

class GameStateResponse(BaseModel):
    id: int
    user_id: str
    board: list
    initial_puzzle: list
    time_played: int
    level: str
    created_at: datetime
    is_hidden: bool

    class Config:
        orm_mode = True

# Dependency to get database session
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# Endpoints
@app.post("/game", response_model=GameStateResponse)
async def create_game(game: GameStateCreate, db: Session = Depends(get_db)):
    db_game = GameState(
        user_id=game.user_id,
        board=json.dumps(game.board),
        initial_puzzle=json.dumps(game.initial_puzzle),
        time_played=game.time_played,
        level=game.level,
        is_hidden=game.is_hidden  # Lưu trạng thái ẩn
    )
    db.add(db_game)
    db.commit()
    db.refresh(db_game)
    db_game.board = json.loads(db_game.board)
    db_game.initial_puzzle = json.loads(db_game.initial_puzzle)
    return db_game

@app.get("/game/{user_id}", response_model=list[GameStateResponse])
async def get_games(user_id: str, db: Session = Depends(get_db)):
    # Chỉ trả về các bản lưu không ẩn
    games = db.query(GameState).filter(GameState.user_id == user_id, GameState.is_hidden == False).all()
    for game in games:
        game.board = json.loads(game.board)
        game.initial_puzzle = json.loads(game.initial_puzzle)
    return games

@app.put("/game/{game_id}", response_model=GameStateResponse)
async def update_game(game_id: int, game: GameStateUpdate, db: Session = Depends(get_db)):
    db_game = db.query(GameState).filter(GameState.id == game_id).first()
    if not db_game:
        raise HTTPException(status_code=404, detail="Game not found")
    db_game.board = json.dumps(game.board)
    db_game.time_played = game.time_played
    db_game.is_hidden = game.is_hidden  # Cập nhật trạng thái ẩn
    db.commit()
    db.refresh(db_game)
    db_game.board = json.loads(db_game.board)
    db_game.initial_puzzle = json.loads(db_game.initial_puzzle)
    return db_game

@app.delete("/game/{game_id}")
async def delete_game(game_id: int, db: Session = Depends(get_db)):
    db_game = db.query(GameState).filter(GameState.id == game_id).first()
    if not db_game:
        raise HTTPException(status_code=404, detail="Game not found")
    db.delete(db_game)
    db.commit()
    return {"message": "Game deleted"}