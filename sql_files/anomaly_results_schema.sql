/*
  Baza wyników dla panelu WPF (PLAN_NAPRAWCZY, Faza 8).

  Tworzy ją i wypełnia:  python -m anomaly.export.results_db --split test --config full --seed 11 22 33 44 55
  Dane pochodzą wyłącznie z plików zamrożonych (Faza 6) i ewaluacji (Faza 7); nic nie jest tu liczone od nowa.
  Skrypt jest idempotentny: tworzy brakujące tabele i indeksy, istniejących nie zmienia.
  Brak haseł: dostęp przez Windows Authentication.
*/

IF OBJECT_ID(N'dbo.Runs', N'U') IS NULL
CREATE TABLE dbo.Runs (
    RunId              INT IDENTITY(1,1) NOT NULL CONSTRAINT PK_Runs PRIMARY KEY,
    Seed               INT            NOT NULL,
    Split              NVARCHAR(16)   NOT NULL,
    Config             NVARCHAR(64)   NOT NULL,
    CodeCommit         CHAR(40)       NOT NULL,     -- wersja kodu zamrożonej konfiguracji
    CodeDirty          BIT            NOT NULL,
    FrozenSha256       CHAR(64)       NOT NULL,     -- SHA-256 frozen_config.json
    EvaluationCommit   CHAR(40)       NOT NULL,     -- wersja kodu ewaluacji (Faza 7), z której pochodzą score'y
    ExportCommit       CHAR(40)       NOT NULL,     -- wersja kodu eksportera
    ExportedAt         DATETIME2(0)   NOT NULL,
    WindowCount        INT            NOT NULL,
    AnomalousWindows   INT            NOT NULL,
    EventCount         INT            NOT NULL,
    XeJsonPath         NVARCHAR(400)  NULL,         -- eksport LOGGER-a (oryginalne zdarzenia XE)
    CONSTRAINT UQ_Runs UNIQUE (Seed, Split, Config)
);
GO

IF OBJECT_ID(N'dbo.Models', N'U') IS NULL
CREATE TABLE dbo.Models (
    ModelId            INT IDENTITY(1,1) NOT NULL CONSTRAINT PK_Models PRIMARY KEY,
    RunId              INT            NOT NULL CONSTRAINT FK_Models_Runs REFERENCES dbo.Runs(RunId) ON DELETE CASCADE,
    Variant            NVARCHAR(8)    NOT NULL,     -- global / role / user
    Detector           NVARCHAR(16)   NOT NULL,     -- rules / robust / iforest / ocsvm / lof
    Hyperparameters    NVARCHAR(400)  NOT NULL,     -- JSON
    FeatureCount       INT            NOT NULL,
    ValPrAuc           FLOAT          NULL,
    ValMainFpr         FLOAT          NULL,
    ValMainRecall      FLOAT          NULL,
    ValMainMcc         FLOAT          NULL,
    AlertCount         INT            NOT NULL,
    CONSTRAINT UQ_Models UNIQUE (RunId, Variant, Detector)
);
GO

-- D7: próg globalny (grupa ALL) albo wspólny dla roli (grupa = RoleId) w wariantach per rola / per użytkownik.
IF OBJECT_ID(N'dbo.Thresholds', N'U') IS NULL
CREATE TABLE dbo.Thresholds (
    ModelId            INT            NOT NULL CONSTRAINT FK_Thresholds_Models REFERENCES dbo.Models(ModelId) ON DELETE CASCADE,
    GroupKey           NVARCHAR(16)   NOT NULL,
    MainThreshold      FLOAT          NOT NULL,
    Recall80Threshold  FLOAT          NOT NULL,
    ValFpr             FLOAT          NULL,
    ValRecall          FLOAT          NULL,
    CONSTRAINT PK_Thresholds PRIMARY KEY (ModelId, GroupKey)
);
GO

IF OBJECT_ID(N'dbo.Windows', N'U') IS NULL
CREATE TABLE dbo.Windows (
    WindowId              BIGINT IDENTITY(1,1) NOT NULL CONSTRAINT PK_Windows PRIMARY KEY,
    RunId                 INT            NOT NULL CONSTRAINT FK_Windows_Runs REFERENCES dbo.Runs(RunId) ON DELETE CASCADE,
    UserId                NVARCHAR(32)   NOT NULL,
    RoleId                NVARCHAR(16)   NOT NULL,
    WindowStart           DATETIME2(0)   NOT NULL,
    OperationCount        INT            NOT NULL,
    -- etykiety (ground truth z generatora; panel pokazuje je jako ocenę alertu, model ich nie widzi)
    IsAnomaly             BIT            NOT NULL,
    IncidentIds           NVARCHAR(200)  NOT NULL,
    AnomalyClasses        NVARCHAR(200)  NOT NULL,
    Intensities           NVARCHAR(50)   NOT NULL,
    Severity              INT            NOT NULL,
    HardNormal            NVARCHAR(200)  NOT NULL,
    -- składniki RiskScore (D10)
    SeverityWeight        FLOAT          NOT NULL,
    CtxNonPrimaryDevice   BIT            NOT NULL,
    CtxAfterHours         BIT            NOT NULL,
    CtxUnknownIp          BIT            NOT NULL,
    ContextWeight         FLOAT          NOT NULL,
    CONSTRAINT UQ_Windows UNIQUE (RunId, UserId, WindowStart)
);
GO

-- Cechy okna w danym wariancie i ich porównanie z profilem normalnym (TRAIN):
-- jednostki modelu (global / rola / użytkownik) i samego użytkownika.
IF OBJECT_ID(N'dbo.WindowFeatures', N'U') IS NULL
CREATE TABLE dbo.WindowFeatures (
    WindowId           BIGINT         NOT NULL CONSTRAINT FK_WindowFeatures_Windows REFERENCES dbo.Windows(WindowId) ON DELETE CASCADE,
    Variant            NVARCHAR(8)    NOT NULL,
    Feature            NVARCHAR(48)   NOT NULL,
    FeatureGroup       NVARCHAR(16)   NOT NULL,
    Value              FLOAT          NOT NULL,
    RobustZ            FLOAT          NULL,         -- |x − mediana| / skala z detektora robust (D12); NULL dla cech binarnych
    UnitMedian         FLOAT          NOT NULL,     -- profil jednostki modelu (okna TRAIN)
    UnitP01            FLOAT          NOT NULL,
    UnitP99            FLOAT          NOT NULL,
    UserMedian         FLOAT          NULL,         -- profil użytkownika (okna TRAIN tego użytkownika)
    RuleViolated       BIT            NULL,         -- naruszenie reguły D13 (tylko cechy z regułą)
    CONSTRAINT PK_WindowFeatures PRIMARY KEY (WindowId, Variant, Feature)
);
GO

-- Alert = okno z alarmem przy głównym progu (D7) danego modelu.
IF OBJECT_ID(N'dbo.Alerts', N'U') IS NULL
CREATE TABLE dbo.Alerts (
    AlertId            BIGINT IDENTITY(1,1) NOT NULL CONSTRAINT PK_Alerts PRIMARY KEY,
    ModelId            INT            NOT NULL CONSTRAINT FK_Alerts_Models REFERENCES dbo.Models(ModelId) ON DELETE CASCADE,
    WindowId           BIGINT         NOT NULL CONSTRAINT FK_Alerts_Windows REFERENCES dbo.Windows(WindowId),
    RawScore           FLOAT          NOT NULL,
    NormScore          FLOAT          NOT NULL,     -- D8
    TailExcess         FLOAT          NOT NULL,     -- rozstrzyganie remisów norm = 1 (D8)
    RiskScore          FLOAT          NOT NULL,     -- D10: 0,6·norm + 0,25·severity + 0,15·kontekst
    ThresholdGroup     NVARCHAR(16)   NOT NULL,
    MainThreshold      FLOAT          NOT NULL,
    AlarmRecall80      BIT            NOT NULL,
    CONSTRAINT UQ_Alerts UNIQUE (ModelId, WindowId)
);
GO

IF OBJECT_ID(N'dbo.Incidents', N'U') IS NULL
CREATE TABLE dbo.Incidents (
    RunId              INT            NOT NULL CONSTRAINT FK_Incidents_Runs REFERENCES dbo.Runs(RunId) ON DELETE CASCADE,
    IncidentId         NVARCHAR(40)   NOT NULL,
    AnomalyClass       NVARCHAR(32)   NOT NULL,
    Intensity          NVARCHAR(8)    NOT NULL,
    UserId             NVARCHAR(32)   NOT NULL,
    RoleId             NVARCHAR(16)   NOT NULL,
    StartTime          DATETIME2(3)   NOT NULL,
    EndTime            DATETIME2(3)   NOT NULL,
    SeverityLevel      INT            NOT NULL,
    EventCount         INT            NOT NULL,
    Tables             NVARCHAR(400)  NOT NULL,
    Operations         NVARCHAR(100)  NOT NULL,
    CONSTRAINT PK_Incidents PRIMARY KEY (RunId, IncidentId)
);
GO

-- Zdarzenia XE okna (ścieżka alertu: okno -> zdarzenia XE -> tekst SQL).
IF OBJECT_ID(N'dbo.Events', N'U') IS NULL
CREATE TABLE dbo.Events (
    EventUid           NVARCHAR(40)   NOT NULL CONSTRAINT PK_Events PRIMARY KEY,
    WindowId           BIGINT         NOT NULL CONSTRAINT FK_Events_Windows REFERENCES dbo.Windows(WindowId) ON DELETE CASCADE,
    SimTimestamp       DATETIME2(6)   NOT NULL,
    PhysicalTimestamp  NVARCHAR(40)   NOT NULL,     -- znacznik czasu XE w oryginalnym zapisie LOGGER-a (UTC)
    XeEventName        NVARCHAR(40)   NOT NULL,
    XeEventSequence    BIGINT         NOT NULL,
    XeSessionId        INT            NOT NULL,
    XeConnectionId     NVARCHAR(40)   NOT NULL,
    LoginName          NVARCHAR(64)   NOT NULL,
    HostName           NVARCHAR(64)   NOT NULL,
    Device             NVARCHAR(32)   NOT NULL,
    Ip                 NVARCHAR(45)   NOT NULL,
    OperationType      NVARCHAR(8)    NOT NULL,
    TargetTable        NVARCHAR(64)   NOT NULL,
    SqlText            NVARCHAR(4000) NOT NULL,
    DurationUs         BIGINT         NOT NULL,
    CpuTimeUs          BIGINT         NOT NULL,
    LogicalReads       BIGINT         NOT NULL,
    PhysicalReads      BIGINT         NOT NULL,
    Writes             BIGINT         NOT NULL,
    [RowCount]         BIGINT         NOT NULL,
    Result             NVARCHAR(16)   NOT NULL,
    GtIncidentId       NVARCHAR(40)   NOT NULL,
    GtAnomalyClass     NVARCHAR(32)   NOT NULL
);
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'IX_Alerts_Model')
    CREATE INDEX IX_Alerts_Model ON dbo.Alerts(ModelId) INCLUDE (WindowId, NormScore, TailExcess, RiskScore);
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'IX_Events_Window')
    CREATE INDEX IX_Events_Window ON dbo.Events(WindowId, SimTimestamp);
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'IX_Alerts_Window')
    CREATE INDEX IX_Alerts_Window ON dbo.Alerts(WindowId);
GO
