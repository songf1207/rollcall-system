-- =====================================================================
-- 课堂随机点名系统 —— 数据库建表脚本
-- 目标库：MySQL 8.0（db4free.net / Free MySQL Hosting / 本地 MySQL 均可）
-- 用法：db4free 的 phpMyAdmin 里「导入」本文件；或 mysql -h host -u user -p db < schema.sql
-- 说明：脚本会先 DROP 再 CREATE，重复执行等于重置数据，演示前想清空直接用。
-- =====================================================================
SET NAMES utf8mb4;
SET time_zone = '+08:00';

DROP TABLE IF EXISTS write_locks;
CREATE TABLE write_locks (
    id INT NOT NULL PRIMARY KEY,
    revision INT NOT NULL DEFAULT 0
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '单班级写事务互斥行';
INSERT INTO write_locks (id, revision) VALUES (1, 0);

DROP TABLE IF EXISTS draw_candidates;
DROP TABLE IF EXISTS draw_logs;
DROP TABLE IF EXISTS point_logs;
DROP TABLE IF EXISTS rollcalls;
DROP TABLE IF EXISTS sessions;
DROP TABLE IF EXISTS students;

-- ---------------------------------------------------------------------
-- 1. 学生表：名单 + 积分缓存列
--    total_points / call_count 是「缓存列」，真正的账本在 point_logs。
--    每次记分都在同一事务里同时更新账本与缓存，查询排行榜只读这张表。
-- ---------------------------------------------------------------------
CREATE TABLE students (
    id                INT          NOT NULL AUTO_INCREMENT,
    student_no        VARCHAR(20)  NOT NULL COMMENT '学号，幂等导入的唯一键',
    name              VARCHAR(50)  NOT NULL COMMENT '姓名',
    major             VARCHAR(100) NULL     COMMENT '专业',
    class_name        VARCHAR(50)  NULL     COMMENT '班级',
    total_points      DECIMAL(6,1) NOT NULL DEFAULT 0.0 COMMENT '总积分（缓存）',
    call_count        INT          NOT NULL DEFAULT 0   COMMENT '被点名总次数',
    random_call_count INT          NOT NULL DEFAULT 0   COMMENT '随机模式被点名次数（导出用）',
    arrive_count      INT          NOT NULL DEFAULT 0   COMMENT '到达课堂次数',
    transfer_credit   TINYINT      NOT NULL DEFAULT 0   COMMENT '剩余点名转移权',
    created_at        DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at        DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uk_student_no (student_no),
    KEY idx_rank (total_points DESC, call_count DESC)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '学生名单与积分缓存';

-- ---------------------------------------------------------------------
-- 2. 点名场次表：一节课 = 一个 session
--    seq_cursor 只在「按学号顺序」模式下作为游标使用。
-- ---------------------------------------------------------------------
CREATE TABLE sessions (
    id         INT      NOT NULL AUTO_INCREMENT,
    mode       ENUM ('random', 'sequential') NOT NULL DEFAULT 'random',
    seq_cursor INT      NOT NULL DEFAULT 0 COMMENT '顺序模式的游标（已点人数）',
    round_no   INT      NOT NULL DEFAULT 1 COMMENT '当前第几轮（一轮内不重复）',
    draw_count INT      NOT NULL DEFAULT 0 COMMENT '本场已点名次数',
    status     ENUM ('open', 'closed') NOT NULL DEFAULT 'open',
    started_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ended_at   DATETIME NULL,
    PRIMARY KEY (id),
    KEY idx_status (status)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '点名场次';

-- ---------------------------------------------------------------------
-- 3. 点名记录表：谁被点了、答没答到、得了几分
--    uk_round 是「一轮不重复」的数据库级兜底，不靠 Python 里的 set。
-- ---------------------------------------------------------------------
CREATE TABLE rollcalls (
    id               INT          NOT NULL AUTO_INCREMENT,
    session_id       INT          NOT NULL,
    student_id       INT          NOT NULL,
    round_no         INT          NOT NULL DEFAULT 1,
    seq              INT          NOT NULL DEFAULT 1 COMMENT '本场第几次点名',
    mode             ENUM ('random', 'sequential') NOT NULL DEFAULT 'random',
    arrived          TINYINT(1)   NULL     COMMENT '是否到达课堂（答到）',
    repeat_ok        TINYINT(1)   NULL     COMMENT '是否准确复述问题',
    answer_score     DECIMAL(3,1) NULL     COMMENT '回答问题得分 0 / 0.5~3',
    point_delta      DECIMAL(5,1) NOT NULL DEFAULT 0.0 COMMENT '本次净得分',
    status           ENUM ('pending', 'scored', 'transferred') NOT NULL DEFAULT 'pending',
    transferred_from INT          NULL     COMMENT '若本次是转入，记录原被点学生',
    created_at       DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uk_round (session_id, round_no, student_id),
    KEY idx_session_seq (session_id, seq),
    KEY idx_student (student_id),
    CONSTRAINT fk_rollcall_session FOREIGN KEY (session_id) REFERENCES sessions (id) ON DELETE CASCADE,
    CONSTRAINT fk_rollcall_student FOREIGN KEY (student_id) REFERENCES students (id) ON DELETE CASCADE
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '点名记录';

-- ---------------------------------------------------------------------
-- 4. 积分账本：只追加、不改写
--    「改判」不 UPDATE 旧记录，而是补一条 reason='manual' 的冲正记录，
--    这样任何一分都能追溯到是谁、因为什么、什么时候加的。
-- ---------------------------------------------------------------------
CREATE TABLE point_logs (
    id          INT          NOT NULL AUTO_INCREMENT,
    student_id  INT          NOT NULL,
    rollcall_id INT          NULL,
    delta       DECIMAL(5,1) NOT NULL COMMENT '本次增减，可为负',
    reason      ENUM ('arrive', 'repeat', 'answer', 'event', 'transfer', 'manual') NOT NULL,
    note        VARCHAR(200) NULL,
    created_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_student_time (student_id, created_at),
    CONSTRAINT fk_pointlog_student FOREIGN KEY (student_id) REFERENCES students (id) ON DELETE CASCADE,
    CONSTRAINT fk_pointlog_rollcall FOREIGN KEY (rollcall_id) REFERENCES rollcalls (id) ON DELETE SET NULL
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '积分账本（只追加）';

-- ---------------------------------------------------------------------
-- 5. 抽签日志：一次点名 = 一条 draw_log + N 条候选快照
--    这是「证明随机不是编的」的证据链：把权重 w 和随机数 u 一起落库，
--    任何人用 -ln(u)/w 重算一遍都能复现同一个被点者。
-- ---------------------------------------------------------------------
CREATE TABLE draw_logs (
    id         INT          NOT NULL AUTO_INCREMENT,
    session_id INT          NOT NULL,
    round_no   INT          NOT NULL,
    seq        INT          NOT NULL,
    alpha      DECIMAL(4,2) NOT NULL DEFAULT 1.00 COMMENT '权重衰减指数 α',
    algorithm  VARCHAR(40)  NOT NULL DEFAULT 'A-Res' COMMENT 'A-Res / sequential / fallback-uniform',
    winner_id  INT          NOT NULL,
    created_at DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_session (session_id, seq),
    CONSTRAINT fk_draw_session FOREIGN KEY (session_id) REFERENCES sessions (id) ON DELETE CASCADE
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '抽签日志（主记录）';

CREATE TABLE draw_candidates (
    id         INT          NOT NULL AUTO_INCREMENT,
    draw_id    INT          NOT NULL,
    student_id INT          NOT NULL,
    weight     DOUBLE       NOT NULL COMMENT '抽样时的权重 w=1/(1+p)^α',
    rand_u     DOUBLE       NOT NULL COMMENT '本次抽到的随机数 u∈(0,1]',
    sort_key   DOUBLE       NOT NULL COMMENT '排序键 -ln(u)/w，最小者中签',
    PRIMARY KEY (id),
    KEY idx_draw (draw_id),
    CONSTRAINT fk_cand_draw FOREIGN KEY (draw_id) REFERENCES draw_logs (id) ON DELETE CASCADE
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '抽签候选快照（可复算）';

-- ---------------------------------------------------------------------
-- 6. 5 条测试数据（作业要求：导入 5 条测试数据后系统即可演示）
--    正式名单请用 POST /api/students/import 从 Excel 导入。
-- ---------------------------------------------------------------------
INSERT INTO students (student_no, name, major, class_name) VALUES
    ('2023001', '张三', '软件工程', '软工2301'),
    ('2023002', '李四', '软件工程', '软工2301'),
    ('2023003', '王五', '计算机科学与技术', '计科2301'),
    ('2023004', '赵六', '人工智能', '智能2302'),
    ('2023005', '钱七', '软件工程', '软工2302');
