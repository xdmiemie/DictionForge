"""S1 upstream model/loss/ScaledAdam with explicit application LR semantics."""
from pathlib import Path


def create(stage_config, initial, run_dir):
    import torch
    import yaml
    from AR.models.t2s_lightning_module import Text2SemanticLightningModule
    from AR.modules.optim import ScaledAdam

    package = torch.load(initial, map_location='cpu', weights_only=False)
    with open('GPT_SoVITS/configs/s1longer-v2.yaml', encoding='utf-8') as stream:
        effective = yaml.safe_load(stream)
    effective['model'] = dict(package['config']['model'])
    effective['train'].update(batch_size=stage_config['batch_size'], precision=stage_config['precision'],
        accumulate=stage_config['accumulate'], learning_rate=stage_config['learning_rate'], if_dpo=False,
        accumulation_policy='mean_per_actual_cycle_with_epoch_tail_flush', learning_rate_policy='constant')
    effective['optimizer'].update(lr=stage_config['learning_rate'])
    effective['output_dir'] = str(run_dir)
    model = Text2SemanticLightningModule(effective, Path(run_dir), is_train=False)
    model.load_state_dict(package['weight'], strict=True)
    del package
    model = model.cuda().train()
    optimizer = ScaledAdam(model.model.parameters(), lr=stage_config['learning_rate'], betas=(.9, .95),
        clipping_scale=2., parameters_names=[[name for name, _ in model.model.named_parameters()]],
        show_dominant_parameters=False, clipping_update_period=1000)
    scheduler = torch.optim.lr_scheduler.LambdaLR(optimizer, lambda _: 1.)
    return model, optimizer, scheduler, effective


def loss(model, batch, _effective):
    return model.model.forward_old(batch['phoneme_ids'], batch['phoneme_ids_len'], batch['semantic_ids'],
        batch['semantic_ids_len'], batch['bert_feature'])


def inference_package(model, effective, metrics):
    return {'weight': {key: value.detach().cpu().half() for key, value in model.state_dict().items()},
            'config': effective, 'info': f"DictionForge GPT updates={metrics['optimizer_updates']}"}
