"""CPU contract fixtures; actual GPU sentence acceptance is recorded separately."""
import copy
from types import SimpleNamespace

import numpy as np
import pytest
import soundfile as sf

from dictionforge.inference_worker import (
    DEFAULTS, SILENCE_THRESHOLD, InferenceProgress, concatenate_sentences, normalize_sentences,
    observe_model, silence_samples, synthesize_sentences, write_verified_pcm,
)


def request_units():
    text = '  星😀，你好。\n你好。  '
    return {'text': text, 'source_text': text, 'parameters': {'seed': 2**32-1}, 'sentences': [
        {'sentence_id': 'first', 'index': 0, 'text': '星😀，你好。', 'source_start': 2, 'source_end': 8, 'seed': 2**32-1},
        {'sentence_id': 'second', 'index': 1, 'text': '你好。', 'source_start': 9, 'source_end': 12, 'seed': 0},
    ]}


def test_unicode_spans_preserve_raw_text_and_wrapped_seeds():
    request = request_units()
    original = copy.deepcopy(request)
    rows = normalize_sentences(request, dict(DEFAULTS, **request['parameters']))
    assert rows[0]['text'] == request['text'][2:8] and rows[1]['text'] == request['text'][9:12]
    assert rows[1]['seed'] == 0 and request == original
    legacy = normalize_sentences({'text': '  欢迎。\n'}, DEFAULTS)
    assert legacy == [dict(sentence_id='sentence-0000', index=0, text='  欢迎。\n', source_start=0, source_end=6, seed=42)]


@pytest.mark.parametrize('mutation', ['utf16_end', 'wrong_seed', 'duplicate_id', 'overlap', 'wrong_text', 'wrong_index', 'bool_index'])
def test_rejects_changed_sentence_identity(mutation):
    request = request_units()
    if mutation == 'utf16_end':
        request['sentences'][0]['source_end'] += 1
    elif mutation == 'wrong_seed':
        request['sentences'][1]['seed'] = 42
    elif mutation == 'duplicate_id':
        request['sentences'][1]['sentence_id'] = 'first'
    elif mutation == 'overlap':
        request['sentences'][1]['source_start'] = 6
    elif mutation == 'wrong_text':
        request['sentences'][0]['text'] = 'changed'
    elif mutation == 'wrong_index':
        request['sentences'][1]['index'] = 7
    else:
        request['sentences'][0]['index'] = False
    with pytest.raises(ValueError, match='INFERENCE_SENTENCES_INVALID'):
        normalize_sentences(request, dict(DEFAULTS, **request['parameters']))


def test_rejects_source_text_rewrite():
    request = request_units()
    request['source_text'] = request['text'].strip()
    with pytest.raises(ValueError, match='INFERENCE_TEXT_INVALID'):
        normalize_sentences(request, DEFAULTS)


class CPUModelFixture:
    """Only upstream PCM is stubbed; production worker WAV/metadata loop runs."""
    def __init__(self, pcm, progress):
        self.pcm, self.progress, self.inputs, self.closed = pcm, progress, [], []

    def run(self, inputs):
        self.inputs.append(dict(inputs))
        self.progress.normalized_texts = ['真实前端返回文本', str(len(self.inputs))]
        try:
            pcm = self.pcm[len(self.inputs)-1]
            yield 48000, pcm[:2]
            yield 48000, pcm[2:]
        finally:
            self.closed.append(len(self.inputs))


def test_actual_pcm_writes_and_exact_concatenation_retain_silence(tmp_path):
    request = request_units()
    units = normalize_sentences(request, dict(DEFAULTS, **request['parameters']))
    progress = InferenceProgress(request)
    original = [np.array([0, 32, -32768, 77, -32, 0, 0], dtype=np.int16),
                np.array([0, 0, 33, -500, 0], dtype=np.int16)]
    model = CPUModelFixture(original, progress)
    synchronizations = []
    rows, seconds = synthesize_sentences(model, {'sample_steps': 32, 'text_split_method': 'cut0'}, units,
        tmp_path, 'cpu-fixture', progress, lambda: synchronizations.append(True))
    assert len(model.inputs) == len(synchronizations) == 2 and model.closed == [1, 2]
    assert [item['seed'] for item in model.inputs] == [2**32-1, 0]
    assert [item['text'] for item in model.inputs] == [row['text'] for row in units]
    assert [(row['start_sample'], row['end_sample']) for row in rows] == [(0, 7), (7, 12)]
    assert [(row['leading_silence_samples'], row['trailing_silence_samples']) for row in rows] == [(2, 3), (2, 1)]
    assert rows[0]['actual_text'] == '真实前端返回文本1' and rows[1]['actual_text'] == '真实前端返回文本2'
    assert all(row['silence_threshold'] == SILENCE_THRESHOLD and row['audio']['decode_verified'] for row in rows)
    assert seconds >= 0
    audio = concatenate_sentences(rows, tmp_path/'full.wav')
    decoded, rate = sf.read(audio['path'], dtype='int16')
    assert rate == 48000 and np.array_equal(decoded, np.concatenate(original))
    assert audio['samples'] == 12 and audio['seconds'] == 12/48000
    finished = [event for event in progress.events if event['stage'] == 'sentence_completed']
    assert [event['completed_sentences'] for event in finished] == [1, 2]
    assert all(event['total_sentences'] == 2 for event in finished)
    assert progress.current_sentence is None
    # A changed persisted sentence cannot silently enter the concatenated result.
    with open(rows[1]['audio']['path'], 'ab') as stream:
        stream.write(b'changed')
    with pytest.raises(ValueError, match='CHANGED_BEFORE_CONCATENATION'):
        concatenate_sentences(rows, tmp_path/'invalid-full.wav')
    assert not (tmp_path/'invalid-full.wav').exists()


def test_non_silent_threshold_handles_int16_min_without_overflow(tmp_path):
    assert silence_samples(np.array([0, -32768, 32], dtype=np.int16)) == (1, 1)
    assert silence_samples(np.array([0, -32, 32], dtype=np.int16)) == (3, 3)
    with pytest.raises(RuntimeError, match='non-silent'):
        write_verified_pcm(tmp_path/'silent.wav', np.array([0, -32, 32], dtype=np.int16))
    assert not (tmp_path/'silent.wav').exists()


def test_second_sentence_failure_preserves_actual_phase_and_completed_files(tmp_path):
    request = request_units()
    units = normalize_sentences(request, dict(DEFAULTS, **request['parameters']))
    progress = InferenceProgress(request)
    class Frontend:
        def preprocess(self):
            raise RuntimeError('second sentence frontend fixture failure')
    frontend = Frontend()
    progress.observe(frontend, 'preprocess', 'text_frontend')
    class Model(CPUModelFixture):
        def run(self, inputs):
            if len(self.inputs):
                frontend.preprocess()
            yield from super().run(inputs)
    model = Model([np.array([0, 500, 0], dtype=np.int16)], progress)
    with pytest.raises(RuntimeError, match='frontend fixture') as failure:
        synthesize_sentences(model, DEFAULTS, units, tmp_path, 'failure-fixture', progress, lambda: None)
    report = progress.failure(failure.value)
    assert report['phase'] == 'text_frontend'
    assert report['sentence_id'] == 'second' and report['sentence_index'] == 1
    assert report['sentence_text'] == '你好。' and report['actual_seed'] == 0
    assert report['completed_sentences'] == 1 and report['total_sentences'] == 2
    assert report['source_text'] == request['text'] and report['parameters_changed'] is False
    assert len(report['completed_sentence_results']) == 1 and 'audio' not in report
    assert list(tmp_path.glob('*.wav')) == [tmp_path/'sentence-failure-fixture-0000.wav']


def test_decoding_mismatch_never_publishes_output_and_does_not_overwrite(tmp_path, monkeypatch):
    pcm = np.array([0, 1000, -300, 0], dtype=np.int16)
    existing = tmp_path/'existing.wav'
    existing.write_bytes(b'protected output')
    with pytest.raises(FileExistsError):
        write_verified_pcm(existing, pcm)
    assert existing.read_bytes() == b'protected output'
    output = tmp_path/'new.wav'
    real_read = sf.read
    def corrupted_read(*args, **kwargs):
        samples, rate = real_read(*args, **kwargs)
        samples[1] += 1
        return samples, rate
    monkeypatch.setattr(sf, 'read', corrupted_read)
    with pytest.raises(RuntimeError, match='decoded PCM differs'):
        write_verified_pcm(output, pcm)
    assert not output.exists() and not output.with_suffix('.wav.part').exists()
    output.with_suffix('.wav.part').write_bytes(b'prior temporary artifact')
    with pytest.raises(FileExistsError):
        write_verified_pcm(output, pcm)
    assert output.with_suffix('.wav.part').read_bytes() == b'prior temporary artifact'


def test_actual_frontend_capture_excludes_reference_text():
    progress = InferenceProgress({'text': '目标', 'parameters': {}})
    empty = lambda *a, **k: None
    model = SimpleNamespace(set_ref_audio=empty, to_batch=empty, using_vocoder_synthesis=empty,
        audio_postprocess=empty, t2s_model=SimpleNamespace(model=SimpleNamespace(infer_panel_naive_batched=empty)),
        text_preprocessor=SimpleNamespace(segment_and_extract_feature_for_text=lambda *a: ([], None, '参考'),
            preprocess=lambda *a: [{'norm_text': '实际'}, {'norm_text': '目标'}]))
    observe_model(model, progress)
    model.text_preprocessor.segment_and_extract_feature_for_text('参考', 'all_zh')
    assert progress.normalized_texts == []
    model.text_preprocessor.preprocess('目标', 'all_zh')
    assert progress.normalized_texts == ['实际', '目标']
