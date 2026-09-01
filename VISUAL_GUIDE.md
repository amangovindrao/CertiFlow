# CertiFlow Visual Guide

## 🎨 Complete Workflow with Diagrams

### 1️⃣ Installation Flow

```mermaid
flowchart TD
    START[Want to use CertiFlow?] --> CHOICE{Have Python?}
    
    CHOICE -->|No| EXE[Download .exe or .zip]
    EXE --> INSTALL{Prefer?}
    INSTALL -->|Installer| SETUP[Run Setup.exe]
    INSTALL -->|Portable| UNZIP[Unzip anywhere]
    SETUP --> SMART{SmartScreen warning?}
    SMART -->|Yes| CLICK[Click More info → Run anyway]
    SMART -->|No| LAUNCH1[Launch CertiFlow]
    CLICK --> LAUNCH1
    UNZIP --> LAUNCH2[Run CertiFlow.exe]
    
    CHOICE -->|Yes| SOURCE[Clone repository]
    SOURCE --> VENV[Create virtual env]
    VENV --> DEPS[pip install -r requirements.txt]
    DEPS --> RUN[python app.py]
    
    LAUNCH1 --> CONFIG
    LAUNCH2 --> CONFIG
    RUN --> CONFIG
    
    CONFIG[Company Settings prompt] --> FILL[Fill in details]
    FILL --> BRAND[Upload logo/signature]
    BRAND --> READY[Ready to generate! 🎉]
    
    style START fill:#E8F5E9
    style READY fill:#C8E6C9,stroke:#4CAF50,stroke-width:3px
    style EXE fill:#E3F2FD
    style SOURCE fill:#FFF3E0
```

---

### 2️⃣ Document Generation Workflow

```mermaid
flowchart LR
    subgraph Input["📝 Input"]
        NEW[New Candidate] --> FORM[Fill Form]
        EXIST[Existing Intern] --> SELECT[Select from List]
    end
    
    subgraph Generate["⚙️ Generate"]
        FORM --> VALIDATE[Validate Fields]
        SELECT --> REUSE[Reuse Data + ID]
        VALIDATE --> RENDER[Render PDF]
        REUSE --> RENDER
    end
    
    subgraph Output["📄 Output"]
        RENDER --> PDF[Document.pdf]
        PDF --> OPEN[Open/View]
        PDF --> EXPORT[Export PNG/JPG]
        PDF --> STORE[Store in output/]
    end
    
    subgraph Database["💾 Database"]
        RENDER --> SAVE[Save Record]
        SAVE --> ASSIGN[Assign Intern ID]
        ASSIGN --> LOG[Log Generation]
    end
    
    style Input fill:#E3F2FD
    style Generate fill:#FFF3E0
    style Output fill:#E8F5E9
    style Database fill:#F3E5F5
```

---

### 3️⃣ Intern ID System

```mermaid
sequenceDiagram
    participant User
    participant App
    participant Database
    participant Counter
    
    User->>App: Enter "Rahul Kumar"
    App->>Database: Search by name
    
    alt Intern exists
        Database-->>App: Found! ID: SO260005
        App->>User: Auto-fill with SO260005
        Note over App: Same person, same ID
    else New intern
        App->>Counter: Request next ID for 2026
        Counter-->>App: SO260015
        App->>Database: Reserve SO260015
        Database-->>App: Confirmed
        App->>User: Assigned SO260015
    end
    
    User->>App: Generate document
    App->>Database: Link doc to intern ID
```

---

### 4️⃣ Backup & Restore Flow

```mermaid
flowchart TB
    subgraph Source["💻 Source Machine"]
        DATA1[Interns: 17<br/>Certificates: 65<br/>PDFs: 112]
        DATA1 --> EXPORT[📦 Export Backup]
        EXPORT --> ZIP[CertiFlow-Backup.zip<br/>39 MB]
    end
    
    ZIP --> TRANSFER{Transfer Method}
    TRANSFER -->|USB| USB[Copy to USB stick]
    TRANSFER -->|Network| NET[Network share/email]
    TRANSFER -->|Cloud| CLOUD[OneDrive/Dropbox]
    
    USB --> TARGET
    NET --> TARGET
    CLOUD --> TARGET
    
    subgraph Target["💻 Target Machine"]
        TARGET[Copy .zip file] --> IMPORT[📥 Import Backup]
        IMPORT --> VERIFY[🔒 Verify Checksums]
        VERIFY --> MODE{Choose Mode}
        
        MODE -->|Fresh Install| REPLACE[Replace Everything]
        MODE -->|Has Data| MERGE[Merge Missing Only]
        
        REPLACE --> SNAPSHOT1[Snapshot current DB]
        MERGE --> SNAPSHOT2[Snapshot current DB]
        
        SNAPSHOT1 --> RESTORE1[Restore All]
        SNAPSHOT2 --> RESTORE2[Add Missing]
        
        RESTORE1 --> DONE[✅ Restored<br/>17 interns, 65 certs]
        RESTORE2 --> DONE
    end
    
    style Source fill:#E3F2FD
    style Target fill:#E8F5E9
    style ZIP fill:#FFF9C4
    style DONE fill:#C8E6C9,stroke:#4CAF50,stroke-width:3px
```

---

### 5️⃣ Bulk Generation Pipeline

```mermaid
flowchart LR
    subgraph Input["📊 Input Sources"]
        EXCEL[Excel File<br/>name, role, dates]
        MANUAL[Type Manually<br/>in table]
        EXISTING[Multi-Select<br/>from interns]
    end
    
    subgraph Validate["✅ Validate"]
        EXCEL --> CHECK
        MANUAL --> CHECK
        EXISTING --> CHECK
        CHECK[Check All Fields]
        CHECK --> ERRORS{Errors?}
        ERRORS -->|Yes| SHOW[Show in Red]
        ERRORS -->|No| READY
        SHOW --> FIX[Fix Issues]
        FIX --> CHECK
    end
    
    subgraph Generate["⚡ Generate"]
        READY[All Valid] --> THREAD[Background Thread]
        THREAD --> BATCH[Generate Each]
        BATCH --> PROGRESS[Update Progress]
    end
    
    subgraph Output["📁 Output"]
        PROGRESS --> FOLDER[output/2026-08-30/]
        FOLDER --> PDFS[100+ PDFs]
        PDFS --> ZIP2[Auto-ZIP All]
    end
    
    style Input fill:#E3F2FD
    style Validate fill:#FFF3E0
    style Generate fill:#F3E5F5
    style Output fill:#E8F5E9
```

---

### 6️⃣ Template Designer Workflow

```mermaid
flowchart TD
    START[Open Template Designer] --> CHOICE{Start From?}
    
    CHOICE -->|Built-in| PICK[Pick Document Type]
    CHOICE -->|Blank| CANVAS[Blank Canvas]
    
    PICK --> LOAD[Load Template]
    LOAD --> EDIT
    CANVAS --> EDIT
    
    subgraph Edit["🎨 Design"]
        EDIT[Drag Elements] --> TEXT[Add Text Boxes]
        TEXT --> IMAGE[Add Images]
        IMAGE --> SHAPE[Add Shapes/Lines]
        SHAPE --> QR[Add QR Code]
        QR --> STYLE[Style: Font, Color, Size]
    end
    
    STYLE --> PREVIEW[Live Preview]
    PREVIEW --> ADJUST{Looks Good?}
    ADJUST -->|No| EDIT
    ADJUST -->|Yes| SAVE[Save Template]
    
    SAVE --> NAME[Give it a Name]
    NAME --> STORED[Stored in templates/]
    STORED --> AVAILABLE[Available Everywhere]
    
    AVAILABLE --> CREATE[Create Document]
    AVAILABLE --> BULK[Bulk Generator]
    AVAILABLE --> INTERNS[Interns Directory]
    
    style START fill:#E8F5E9
    style Edit fill:#E3F2FD
    style AVAILABLE fill:#FFF3E0
```

---

### 7️⃣ Data Location Logic

```mermaid
flowchart TB
    LAUNCH[Launch CertiFlow] --> CHECK{Check Environment}
    
    CHECK -->|CERTIFLOW_DATA set| ENV[Use $CERTIFLOW_DATA]
    CHECK -->|installed.marker exists| INSTALLED[Use %LOCALAPPDATA%\CertiFlow]
    CHECK -->|Neither| PORTABLE[Use folder with .exe]
    
    ENV --> FOLDERS
    INSTALLED --> FOLDERS
    PORTABLE --> FOLDERS
    
    subgraph FOLDERS["📁 Data Folders Created"]
        direction LR
        CO[company/] --> OU[output/]
        OU --> TE[templates/]
        TE --> LO[logs/]
        LO --> BA[Backup & Restore/]
    end
    
    FOLDERS --> FIRST{First Run?}
    FIRST -->|Yes| SEED[Seed Example]
    FIRST -->|No| LOAD[Load Settings]
    
    SEED --> PROMPT[Prompt Settings]
    LOAD --> READY[Ready]
    PROMPT --> READY
    
    style ENV fill:#E3F2FD
    style INSTALLED fill:#E8F5E9
    style PORTABLE fill:#FFF3E0
    style READY fill:#C8E6C9,stroke:#4CAF50,stroke-width:3px
```

---

### 8️⃣ Build & Package Pipeline

```mermaid
flowchart LR
    subgraph Source["📂 Source Code"]
        PY[23 Python modules] --> SPEC[CertiFlow.spec]
    end
    
    SPEC --> PYINST[PyInstaller]
    PYINST --> BUNDLE[Bundle .exe + deps]
    BUNDLE --> FOLDER[dist/CertiFlow/]
    
    FOLDER --> CHECK[PII Check]
    CHECK --> SCAN{Contains<br/>personal data?}
    SCAN -->|Yes| ABORT[❌ Refuse to build]
    SCAN -->|No| PASS[✅ Clean]
    
    PASS --> ZIP[Create portable.zip]
    PASS --> INNO[Inno Setup]
    
    ZIP --> RELEASE1[portable.zip<br/>37 MB]
    INNO --> RELEASE2[Setup.exe<br/>28 MB]
    
    RELEASE1 --> READY
    RELEASE2 --> READY
    READY[dist/release/]
    
    style Source fill:#E3F2FD
    style FOLDER fill:#FFF3E0
    style CHECK fill:#F3E5F5
    style READY fill:#C8E6C9,stroke:#4CAF50,stroke-width:3px
    style ABORT fill:#FFEBEE,stroke:#F44336,stroke-width:2px
```

---

## 📱 UI Screen Flow

### Navigation Structure

```
┌─────────────────────────────────────────────────────────────┐
│  CertiFlow                                           ☀/🌙   │
│  ScaleOn                                                    │
│ ───────────────────────────────────────────────────────── │
│                                                             │
│  📄  Create Document   ◄─── Most used                      │
│  🎨  Template Designer                                      │
│  👥  Interns           ◄─── View all records               │
│  ⚡  Bulk Generator    ◄─── Process many at once           │
│  🔎  Verification      ◄─── Look up by ID                  │
│  📁  Generated Files   ◄─── Open past documents            │
│  💾  Backup & Restore  ◄─── Migrate/archive                │
│  🏢  Company Settings  ◄─── One-time setup                 │
│  ℹ   About                                                  │
│                                                             │
│  Keyboard: Ctrl+1 through Ctrl+9 to switch pages           │
└─────────────────────────────────────────────────────────────┘
```

---

## 🎯 Common User Journeys

### Journey 1: First-Time User
```
1. Download & Install
   └─> Company Settings
       └─> Upload branding
           └─> Create first document
               └─> View in Generated Files
```

### Journey 2: Bulk Generation
```
1. Prepare Excel with candidate list
   └─> Bulk Generator → Import
       └─> Fix any validation errors
           └─> Generate All (background)
               └─> Download auto-created ZIP
```

### Journey 3: Migrating to New Computer
```
1. Old Computer: Backup & Restore → Export
   └─> Copy .zip to new computer
       └─> New Computer: Import Backup
           └─> Choose Merge or Replace
               └─> All data restored with same IDs
```

### Journey 4: Creating Custom Design
```
1. Template Designer
   └─> Start from existing or blank
       └─> Drag text/images/shapes
           └─> Style everything
               └─> Save with name
                   └─> Now available in Create Document
```

### Journey 5: Looking Up an Issued Certificate
```
1. Verification page
   └─> Enter Intern ID (e.g., SO260008)
       └─> See full details + all certificates
           └─> Open PDF button → view document
```

---

## 🔒 Security & Privacy

```mermaid
flowchart TB
    START[User Data] --> LOCAL[100% Local Storage]
    LOCAL --> NO_NET[No Internet Required]
    LOCAL --> NO_CLOUD[No Cloud Upload]
    LOCAL --> NO_TRACK[No Telemetry]
    
    LOCAL --> FILES[Your Files]
    FILES --> WHO{Who Can Access?}
    WHO -->|Only You| SECURE[Your Computer Only]
    WHO -->|Share| BACKUP[Backup File You Send]
    
    SECURE --> CONTROL[Full Control]
    BACKUP --> ENCRYPT{Encrypt?}
    ENCRYPT -->|Yes| SAFE[Use 7-Zip/BitLocker]
    ENCRYPT -->|No| PLAIN[Plain ZIP]
    
    CONTROL --> DELETE[Can Delete Anytime]
    SAFE --> SHARE[Safe to Transfer]
    PLAIN --> SHARE
    
    style START fill:#E8F5E9
    style SECURE fill:#C8E6C9,stroke:#4CAF50,stroke-width:2px
    style NO_NET fill:#E3F2FD
    style NO_CLOUD fill:#E3F2FD
    style NO_TRACK fill:#E3F2FD
```

---

## 📊 System Architecture Layers

```
┌───────────────────────────────────────────────────────────┐
│                    🖥️ User Interface                      │
│   CustomTkinter · Dark/Light Theme · Live Previews       │
└───────────────────────────────────────────────────────────┘
                           ↓
┌───────────────────────────────────────────────────────────┐
│                 🎯 Business Logic Layer                   │
│   Validation · ID Generation · Batch Processing           │
│   Import/Export · Backup/Restore · Template Engine        │
└───────────────────────────────────────────────────────────┘
                           ↓
┌───────────────────────────────────────────────────────────┐
│                  📄 Document Generation                    │
│   ReportLab (PDF) · Pillow (Images) · Fonts Embedded     │
│   pypdfium2 (Preview) · Custom Renderers                 │
└───────────────────────────────────────────────────────────┘
                           ↓
┌───────────────────────────────────────────────────────────┐
│                   💾 Data Persistence                     │
│   SQLite (IDs) · JSON (Settings) · File System (PDFs)    │
│   Snapshots (Backup) · Archives (Transfer)               │
└───────────────────────────────────────────────────────────┘
```

---

<div align="center">

## 🎉 Visual Guide Complete!

This guide covers all major workflows, diagrams, and user journeys.

**For more details, see:**
- [README.md](README.md) - Full documentation
- [docs/SETUP.md](docs/SETUP.md) - Build guide
- [GitHub Repository](https://github.com/amangovindrao/CertiFlow)

</div>
