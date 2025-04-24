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
        
# Hàm tìm ứng cử viên
def get_candidates(board, row, col):
    candidates = set(range(1, 10))
    
    # Kiểm tra hàng
    candidates -= set(board[row])
    
    # Kiểm tra cột
    candidates -= set(board[i][col] for i in range(9))
    
    # Kiểm tra ô 3x3
    start_row, start_col = 3 * (row // 3), 3 * (col // 3)
    for i in range(start_row, start_row + 3):
        for j in range(start_col, start_col + 3):
            candidates -= {board[i][j]}
    
    return list(candidates)

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

@app.get("/hint/{game_id}")
async def get_hint(game_id: int, db: Session = Depends(get_db)):
    db_game = db.query(GameState).filter(GameState.id == game_id).first()
    if not db_game:
        raise HTTPException(status_code=404, detail="Game not found")
    
    board = json.loads(db_game.board)
    
    # Tìm ô trống có ít ứng cử viên nhất
    min_candidates = 10
    hint_cell = None
    hint_value = None
    explanation = ""
    
    for i in range(9):
        for j in range(9):
            if board[i][j] == 0:
                candidates = get_candidates(board, i, j)
                if len(candidates) < min_candidates and len(candidates) > 0:
                    min_candidates = len(candidates)
                    hint_cell = {"row": i, "col": j}
                    hint_value = candidates[0]
    
    if not hint_cell:
        raise HTTPException(status_code=400, detail="No hint available")
    
    # Tạo lời giải thích
    row, col = hint_cell["row"], hint_cell["col"]
    explanation = f"Ô ở hàng {row + 1}, cột {col + 1} có thể điền số {hint_value} vì: "
    
    # Kiểm tra hàng
    row_nums = [num for num in board[row] if num != 0]
    explanation += f"Hàng {row + 1} chứa các số {row_nums or 'không có số nào'}. Số {hint_value} không có trong hàng này. "
    
    # Kiểm tra cột
    col_nums = [board[i][col] for i in range(9) if board[i][col] != 0]
    explanation += f"Cột {col + 1} chứa các số {col_nums or 'không có số nào'}. Số {hint_value} không có trong cột này. "
    
    # Kiểm tra ô 3x3
    start_row, start_col = 3 * (row // 3), 3 * (col // 3)
    box_nums = [board[i][j] for i in range(start_row, start_row + 3) for j in range(start_col, start_col + 3) if board[i][j] != 0]
    explanation += f"Ô 3x3 chứa ô này có các số {box_nums or 'không có số nào'}. Số {hint_value} không có trong ô 3x3 này."
    
    return {
        "row": row,
        "col": col,
        "value": hint_value,
        "explanation": explanation
    }

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