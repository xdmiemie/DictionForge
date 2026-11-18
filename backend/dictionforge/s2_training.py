"""Official v4 CFM LoRA route, preserving every upstream trainable branch."""
import copy
import json
from pathlib import Path


def create(stage_config, initial, run_dir):
    import torch
    from peft import LoraConfig, get_peft_model
    from module.models import SynthesizerTrnV3
    from process_ckpt import load_sovits_new, get_sovits_version_from_path_fast

    base = Path('GPT_SoVITS/pretrained_models/gsv-v4-pretrained/s2Gv4.pth').resolve()
    package = load_sovits_new(str(base))
    effective = copy.deepcopy(package['config'])
    effective['model']['version'] = 'v4'
    effective['model']['semantic_frame_rate'] = '25hz'
    effective['train'].update(batch_size=stage_config['batch_size'], learning_rate=stage_config['learning_rate'],
        fp16_run=stage_config['precision'] == 'fp16', lora_rank=stage_config['rank'], grad_ckpt=False)
    effective['data']['exp_dir'] = str(run_dir)
    effective['save_weight_dir'] = str(Path(run_dir) / 'snapshots')
    model = SynthesizerTrnV3(effective['data']['filter_length'] // 2 + 1,
        effective['train']['segment_size'] // effective['data']['hop_length'],
        n_speakers=effective['data']['n_speakers'], **effective['model'])
    model.load_state_dict(package['weight'], strict=True)
    from .initialization import loaded_weights_report
    from .audio import digest
    initialization = dict(base_load_count=1, lora_wrap_count=1, source_load_count=0,
                          base=str(base), source=str(initial), base_check=loaded_weights_report(model, package['weight']))
    del package
    rank = stage_config['rank']
    model.cfm = get_peft_model(model.cfm, LoraConfig(target_modules=['to_k', 'to_q', 'to_v', 'to_out.0'],
        r=rank, lora_alpha=rank, init_lora_weights=True))
    if digest(Path(initial)) != digest(base):
        _, version, is_lora = get_sovits_version_from_path_fast(str(initial))
        if version != 'v4' or not is_lora:
            raise ValueError('MODEL_INITIALIZATION_INVALID: source SoVITS must be compatible v4 LoRA')
        source = load_sovits_new(str(initial))
        if source.get('lora_rank') != rank:
            raise ValueError('MODEL_RANK_MISMATCH')
        expected = model.state_dict()
        trainable = {key for key, parameter in model.named_parameters() if parameter.requires_grad}
        if not trainable.issubset(source['weight']) or set(source['weight']) - set(expected):
            raise ValueError('MODEL_INITIALIZATION_INVALID: incomplete or unexpected source keys')
        for key, value in source['weight'].items():
            if expected[key].shape != value.shape:
                raise ValueError(f'MODEL_INITIALIZATION_INVALID: shape {key}')
        model.load_state_dict(source['weight'], strict=False)
        initialization.update(loaded_weights_report(model, source['weight']), source_load_count=1)
        del source
    effective['initialization'] = initialization
    model = model.cuda().train()
    optimizer = torch.optim.AdamW((p for p in model.parameters() if p.requires_grad),
        lr=stage_config['learning_rate'], betas=tuple(effective['train']['betas']), eps=effective['train']['eps'])
    scheduler = torch.optim.lr_scheduler.ExponentialLR(optimizer, gamma=effective['train']['lr_decay'])
    return model, optimizer, scheduler, effective


def loss(model, batch, effective):
    ssl, spec, mel, ssl_lengths, spec_lengths, text, text_lengths, mel_lengths = batch
    return model(ssl, spec, mel, ssl_lengths, spec_lengths, text, text_lengths, mel_lengths,
                 use_grad_ckpt=effective['train']['grad_ckpt']), None


def inference_package(model, effective, metrics):
    frozen = {key for key, parameter in model.named_parameters() if not parameter.requires_grad}
    weights = {key: value.detach().cpu().half() for key, value in model.state_dict().items()
               if key not in frozen and 'enc_q' not in key}
    return {'weight': weights, 'config': effective, 'lora_rank': effective['train']['lora_rank'],
            'info': f"DictionForge v4 updates={metrics['optimizer_updates']}"}
