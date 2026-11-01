from pathlib import Path

from tripo_fleet import fleet, runner


def test_finish_preserves_fbx_and_records_completed_normalization(tmp_path, monkeypatch):
    vehicle = fleet.Vehicle('car', 'test', 4.5, 2, 1.6, {}, {'quad': True, 'face_limit': 25000})
    class Client:
        def wait(self, *args, **kwargs):
            return {'status': 'success', 'credits_consumed': 110,
                    'output': {'model_url': 'https://example.test/model.fbx?signature=x'}}
    downloads = []
    def download(url, dest):
        downloads.append(dest.name)
        dest.write_bytes(b'Kaydara FBX Binary  ')
        return dest.stat().st_size
    def normalize(ws, v, attempt):
        assert (attempt / 'raw.fbx').read_bytes().startswith(b'Kaydara')
        assert not (attempt / 'raw.glb').exists()
        (attempt / 'car_tripo.glb').write_bytes(b'glTF')
    monkeypatch.setattr(runner, 'download', download)
    monkeypatch.setattr(runner, 'normalize_attempt', normalize)
    ws = runner.Workspace(tmp_path)
    attempt = ws.new_attempt('car')
    state = runner.finish(Client(), ws, vehicle, attempt, {'task_id': 'test'}, 10)
    assert state['normalization_status'] == 'complete'
    assert state['raw_file'] == 'raw.fbx'
    assert runner.succeeded(ws, 'car') == attempt
    # Resume/re-query must not replace a retained native download.
    runner.finish(Client(), ws, vehicle, attempt, state, 10)
    assert downloads == ['raw.fbx']
