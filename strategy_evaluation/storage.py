"""New isolated append-only evaluation events; never updates raw/signal records."""
import os
import tempfile
from pathlib import Path
from upbit_b.feature_contracts import dumps
from upbit_c.research_archive import isolated
from .engine import validate_evaluation
from .aggregate import latest


def append(root, event):
    validate_evaluation(event)
    from upbit_c.research_release import no_symlinks
    no_symlinks(Path(root))
    root=isolated(root)
    if (Path(__file__).resolve().parents[1] == root or Path(__file__).resolve().parent in (root,*root.parents)):
        raise ValueError('source namespace protected')
    directory=root/event['strategy']/event['evaluation_id'];directory.mkdir(parents=True,exist_ok=True)
    no_symlinks(directory)
    lock=directory/'.lock'
    try:lock.mkdir()
    except FileExistsError:raise ValueError('evaluation writer busy; inspect stale lock, never auto-reset') from None
    try:return _append(directory,event)
    finally:lock.rmdir()


def _append(directory,event):
    existing=[]
    for p in directory.glob('*.json'):
        import json
        e=json.loads(p.read_bytes());validate_evaluation(e);existing.append(e)
    # Semantic conflicts/terminal rollback fail before a write.
    latest([*existing,event])
    if any(e['status']=='MATURED' for e in existing):return 'REPLAY_NOOP'
    path=directory/(event['event_id']+'.json');data=(dumps(event)+'\n').encode()
    if path.exists():
        if path.read_bytes()!=data:raise ValueError('append-only conflict')
        return 'REPLAY_NOOP'
    temp=None
    try:
        with tempfile.NamedTemporaryFile(dir=directory,delete=False) as f:
            temp=f.name;f.write(data);f.flush();os.fsync(f.fileno())
        try:os.link(temp,path)
        except FileExistsError:
            if path.read_bytes()!=data:raise ValueError('append-only conflict')
            return 'REPLAY_NOOP'
        return 'CREATED'
    finally:
        if temp is not None:os.unlink(temp)
