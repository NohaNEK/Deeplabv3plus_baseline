from tqdm import tqdm
import network
import utils
import os
import random
import argparse
import numpy as np

from torch.utils import data
from datasets import VOCSegmentation, Cityscapes, GTA, GTAV
from utils import ext_transforms as et
from metrics import StreamSegMetrics
import pandas as pd

import torch
import torch.nn as nn
from utils.visualizer import Visualizer

from PIL import Image
import matplotlib
import matplotlib.pyplot as plt
import cv2
from tensorboardX import SummaryWriter
import pandas as pd

def get_argparser():
    parser = argparse.ArgumentParser()

    # Datset Options
    parser.add_argument("--data_root", type=str, default='/media/fahad/Crucial X8/Mohamed/GTA/',
                        help="path to Dataset")
    parser.add_argument("--dataset", type=str, default='voc',
                        choices=['voc', 'cityscapes'], help='Name of dataset')
    parser.add_argument("--num_classes", type=int, default=None,
                        help="num classes (default: None)")

    # Deeplab Options
    available_models = sorted(name for name in network.modeling.__dict__ if name.islower() and \
                              not (name.startswith("__") or name.startswith('_')) and callable(
                              network.modeling.__dict__[name])
                              )
    parser.add_argument("--model", type=str, default='deeplabv3plus_mobilenet',
                        choices=available_models, help='model name')
    parser.add_argument("--separable_conv", action='store_true', default=False,
                        help="apply separable conv to decoder and aspp")
    parser.add_argument("--output_stride", type=int, default=16, choices=[8, 16])

    # Train Options
    parser.add_argument("--test_only", action='store_true', default=False)
    parser.add_argument("--save_val_results", action='store_true', default=False,
                        help="save segmentation results to \"./results\"")
    parser.add_argument("--total_itrs", type=int, default=100e3,
                        help="epoch number (default: 30k)")
    parser.add_argument("--lr", type=float, default=0.01,
                        help="learning rate (default: 0.01)")
    parser.add_argument("--lr_policy", type=str, default='poly', choices=['poly', 'step'],
                        help="learning rate scheduler policy")
    parser.add_argument("--step_size", type=int, default=10000)
    parser.add_argument("--crop_val", action='store_true', default=False,
                        help='crop validation (default: False)')
    parser.add_argument("--batch_size", type=int, default=6,
                        help='batch size (default: 16)')
    parser.add_argument("--val_batch_size", type=int, default=6,
                        help='batch size for validation (default: 4)')
    parser.add_argument("--crop_size", type=int, default=768)

    parser.add_argument("--ckpt", default=None, type=str,
                        help="restore from checkpoint")
    parser.add_argument("--continue_training", action='store_true', default=False)

    parser.add_argument("--loss_type", type=str, default='cross_entropy',
                        choices=['cross_entropy', 'focal_loss'], help="loss type (default: False)")
    parser.add_argument("--gpu_id", type=str, default='0',
                        help="GPU ID")
    parser.add_argument("--weight_decay", type=float, default=5e-4,
                        help='weight decay (default: 1e-4)')
    parser.add_argument("--random_seed", type=int, default=10,
                        help="random seed (default: 1)")
    parser.add_argument("--print_interval", type=int, default=10,
                        help="print interval of loss (default: 10)")
    parser.add_argument("--val_interval", type=int, default=1000,
                        help="epoch interval for eval (default: 100)")
    parser.add_argument("--download", action='store_true', default=False,
                        help="download datasets")

    # PASCAL VOC Options
    parser.add_argument("--year", type=str, default='2012',
                        choices=['2012_aug', '2012', '2011', '2009', '2008', '2007'], help='year of VOC')

    # Visdom options
    parser.add_argument("--enable_vis", action='store_true', default=False,
                        help="use visdom for visualization")
    parser.add_argument("--vis_port", type=str, default='13570',
                        help='port for visdom')
    parser.add_argument("--vis_env", type=str, default='main',
                        help='env for visdom')
    parser.add_argument("--vis_num_samples", type=int, default=8,
                        help='number of samples for visualization (default: 8)')
    return parser


def get_dataset(opts):
    """ Dataset And Augmentation
    """
    if opts.dataset == 'voc':
        train_transform = et.ExtCompose([
            # et.ExtResize(size=opts.crop_size),
            et.ExtRandomScale((0.5, 2.0)),
            et.ExtRandomCrop(size=(opts.crop_size, opts.crop_size), pad_if_needed=True),
            et.ExtRandomHorizontalFlip(),
            et.ExtToTensor(),
            et.ExtNormalize(mean=[0.485, 0.456, 0.406],
                            std=[0.229, 0.224, 0.225]),
        ])
        if opts.crop_val:
            val_transform = et.ExtCompose([
                et.ExtResize(opts.crop_size),
                et.ExtCenterCrop(opts.crop_size),
                et.ExtToTensor(),
                et.ExtNormalize(mean=[0.485, 0.456, 0.406],
                                std=[0.229, 0.224, 0.225]),
            ])
        else:
            val_transform = et.ExtCompose([
                et.ExtToTensor(),
                et.ExtNormalize(mean=[0.485, 0.456, 0.406],
                                std=[0.229, 0.224, 0.225]),
            ])
        train_dst = VOCSegmentation(root=opts.data_root, year=opts.year,
                                    image_set='train', download=opts.download, transform=train_transform)
        val_dst = VOCSegmentation(root=opts.data_root, year=opts.year,
                                  image_set='val', download=False, transform=val_transform)

    if opts.dataset == 'cityscapes':
        train_transform = et.ExtCompose([
            et.ExtResize(size= (1914,1052) ),
            et.ExtRandomCrop(size=(768,768)),
            et.ExtColorJitter(brightness=0.5, contrast=0.5, saturation=0.5),
            et.ExtRandomHorizontalFlip(),
            et.ExtToTensor(),
            et.ExtNormalize(mean=[0.485, 0.456, 0.406],
                            std=[0.229, 0.224, 0.225]),
        ])
     
        val_transform = et.ExtCompose([
            et.ExtResize( (768,768)  ),
            et.ExtToTensor(),
            et.ExtNormalize(mean=[0.485, 0.456, 0.406],
                            std=[0.229, 0.224, 0.225]),
        ])

        train_dst = GTA(root=opts.data_root,
                               split='all', transform=train_transform)
        val_dst = Cityscapes(root='/media/fahad/Crucial X8/datasets/cityscapes/',
                        split='val', transform=val_transform)
        # val_dst = GTAV(root='/media/fahad/Crucial X8/gta5/gta/',
        #                      split='sub_bdd', transform=val_transform)
    return train_dst, val_dst

def add_gta_infos_in_tensorboard(writer,imgs,labels,outputs,cur_itrs,denorm,train_loader):
        img=imgs[0].detach().cpu().numpy()
        img=(denorm(img)*255).astype(np.uint8)
        #writer.add_image('input image',img,cur_itrs,dataformats='CHW')
        # writer.add_image('rec image',img,cur_itrs,dataformats='CHW')
        # writer.add_image('coco image',rgb_lb,cur_itrs,dataformats='CHW')
        lbs=labels[0].detach().cpu().numpy()
        lbs=train_loader.dataset.decode_target(lbs).astype('uint8')
        #writer.add_image('ground truth',train_loader.dataset.decode_target(lbs).astype('uint8'),cur_itrs,dataformats='HWC')
        pred=outputs.detach().max(1)[1].cpu().numpy()
        pred = train_loader.dataset.decode_target(pred[0]).astype('uint8')
        #writer.add_image('pred',pred,cur_itrs,dataformats='HWC')
        
        
        img_grid =  [img,np.transpose(lbs,(2,0,1)),np.transpose(pred,(2,0,1))]
        writer.add_images('test sample gta 0',img_grid,cur_itrs,dataformats='CHW')
        img=imgs[1].detach().cpu().numpy()
        img=(denorm(img)*255).astype(np.uint8)
        lbs=labels[1].detach().cpu().numpy()
        lbs=train_loader.dataset.decode_target(lbs).astype('uint8')
        pred=outputs.detach().max(1)[1].cpu().numpy()
        pred = train_loader.dataset.decode_target(pred[1]).astype('uint8')

        img_grid =  [img,np.transpose(lbs,(2,0,1)),np.transpose(pred,(2,0,1))]
        writer.add_images('test sample gta 1',img_grid,cur_itrs,dataformats='CHW')
def add_cs_in_tensorboard(writer,imgs,labels,outputs,cur_itrs,denorm,train_loader,i):
    if imgs[i] == None :
        print("img none", i)
    # print(imgs[i])

    img=imgs[i].detach().cpu().numpy()

    img=(denorm(img)*255).astype(np.uint8)
    lbs=labels[i].detach().cpu().numpy()
    lbs=train_loader.dataset.decode_target(lbs).astype('uint8')
    pred=outputs.detach().max(1)[1].cpu().numpy()
    pred=train_loader.dataset.decode_target(pred[i]).astype('uint8')


    res_grid=[img,np.transpose(lbs,(2,0,1)),np.transpose(pred,(2,0,1))]
    
    writer.add_images('test sample cityscapes '+str(i),res_grid,cur_itrs,dataformats='CHW')
def create_colormap(feat):
    # cmap= plt.get_cmap('viridis')
    cmap= plt.get_cmap('jet')

    feat_map = cmap(feat)

    feat_map=(feat_map*255).astype(np.uint8)
    return feat_map
def validate(opts, model, loader, device, metrics,denorm=None,writer=None, cur_itrs=0,ret_samples_ids=None):
    """Do validation and return specified samples"""
    metrics.reset()
    ret_samples = []
    if opts.save_val_results:
        if not os.path.exists('results'):
            os.mkdir('results')
        denorm = utils.Denormalize(mean=[0.485, 0.456, 0.406],
                                   std=[0.229, 0.224, 0.225])
        img_id = 0

    with torch.no_grad():
        for i, (images, labels) in tqdm(enumerate(loader)):

            images = images.to(device, dtype=torch.float32)
            labels = labels.to(device, dtype=torch.long)

            outputs,_ = model(images)
            preds = outputs.detach().max(dim=1)[1].cpu().numpy()
            targets = labels.cpu().numpy()

            metrics.update(targets, preds)
            if i <4 :
                add_cs_in_tensorboard(writer,images,labels,outputs,cur_itrs,denorm,loader,i)
            if ret_samples_ids is not None and i in ret_samples_ids:  # get vis samples
                ret_samples.append(
                    (images[0].detach().cpu().numpy(), targets[0], preds[0]))

            if opts.save_val_results:
                for i in range(len(images)):
                    image = images[i].detach().cpu().numpy()
                    target = targets[i]
                    pred = preds[i]

                    image = (denorm(image) * 255).transpose(1, 2, 0).astype(np.uint8)
                    target = loader.dataset.decode_target(target).astype(np.uint8)
                    pred = loader.dataset.decode_target(pred).astype(np.uint8)

                    Image.fromarray(image).save('results/%d_image.png' % img_id)
                    Image.fromarray(target).save('results/%d_target.png' % img_id)
                    Image.fromarray(pred).save('results/%d_pred.png' % img_id)

                    fig = plt.figure()
                    plt.imshow(image)
                    plt.axis('off')
                    plt.imshow(pred, alpha=0.7)
                    ax = plt.gca()
                    ax.xaxis.set_major_locator(matplotlib.ticker.NullLocator())
                    ax.yaxis.set_major_locator(matplotlib.ticker.NullLocator())
                    plt.savefig('results/%d_overlay.png' % img_id, bbox_inches='tight', pad_inches=0)
                    plt.close()
                    img_id += 1

        score = metrics.get_results()
    return score, ret_samples

def add_feats(writer,feats,name,cur_itrs):
        
        # for f in feats:
            #f1=feats[0]
            f_b=feats['out'][0].mean(dim=0)
            f_b= (f_b-f_b.min())/(f_b.max()-f_b.min())         
            writer.add_image('feat_out_'+name,create_colormap(f_b.detach().cpu().numpy()),cur_itrs,dataformats='HWC')
            f_b=feats['low_level'][0].mean(dim=0)
            f_b= (f_b-f_b.min())/(f_b.max()-f_b.min())         
            writer.add_image('feat_lowl_'+name,create_colormap(f_b.detach().cpu().numpy()),cur_itrs,dataformats='HWC')
            # f_b=torch.sum(feats[1][0],dim=0)
            # f_b= (f_b-f_b.min())/(f_b.max()-f_b.min())         
            # writer.add_image('feat_backbone_l2_'+name,create_colormap(f_b.detach().cpu().numpy()),cur_itrs,dataformats='HWC')
def writer_add_features(writer, name, tensor_feat, iterations):
    feat_img = tensor_feat[0].detach().cpu().numpy()
    # img_grid = self.make_grid(feat_img)
    feat_img = np.sum(feat_img,axis=0)
    feat_img = feat_img -np.min(feat_img)
    img_grid = 255*feat_img/np.max(feat_img)
    img_grid = cv2.applyColorMap(np.array(img_grid, dtype=np.uint8), cv2.COLORMAP_JET)
    writer.add_image(name, img_grid, iterations, dataformats='HWC')
def main():
    opts = get_argparser().parse_args()
    if opts.dataset.lower() == 'voc':
        opts.num_classes = 21
    elif opts.dataset.lower() == 'cityscapes':
        opts.num_classes = 19

    # Setup visualization
    vis = Visualizer(port=opts.vis_port,
                     env=opts.vis_env) if opts.enable_vis else None
    if vis is not None:  # display options
        vis.vis_table("Options", vars(opts))

    os.environ['CUDA_VISIBLE_DEVICES'] = opts.gpu_id
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    print("Device: %s" % device)

    # Setup random seed
    torch.manual_seed(opts.random_seed)
    np.random.seed(opts.random_seed)
    random.seed(opts.random_seed)
    writer = SummaryWriter("/media/fahad/Crucial X8/deeplabv3plus/Deeplabv3plus_baseline/logs/R101_Baseline")

    # Setup dataloader
    if opts.dataset == 'voc' and not opts.crop_val:
        opts.val_batch_size = 1

    train_dst, val_dst = get_dataset(opts)
    train_loader = data.DataLoader(
        train_dst, batch_size=opts.batch_size, shuffle=True, num_workers=2,
        drop_last=True)  # drop_last=True to ignore single-image batches.
    val_loader = data.DataLoader(
        val_dst, batch_size=opts.val_batch_size, shuffle=True, num_workers=2)
    print("Dataset: %s, Train set: %d, Val set: %d" %
          (opts.dataset, len(train_dst), len(val_dst)))

    # Set up model (all models are 'constructed at network.modeling)
    model = network.modeling.__dict__[opts.model](num_classes=opts.num_classes, output_stride=opts.output_stride)
    if opts.separable_conv and 'plus' in opts.model:
        network.convert_to_separable_conv(model.classifier)
    utils.set_bn_momentum(model.backbone, momentum=0.01)

    # Set up metrics
    metrics = StreamSegMetrics(opts.num_classes)

    # Set up optimizer
    optimizer = torch.optim.SGD(params=[
        {'params': model.backbone.parameters(), 'lr': 0.1 * opts.lr},
        {'params': model.classifier.parameters(), 'lr': opts.lr},
    ], lr=opts.lr, momentum=0.9, weight_decay=opts.weight_decay)
    # optimizer = torch.optim.SGD(params=model.parameters(), lr=opts.lr, momentum=0.9, weight_decay=opts.weight_decay)
    # torch.optim.lr_scheduler.StepLR(optimizer, step_size=opts.lr_decay_step, gamma=opts.lr_decay_factor)
    if opts.lr_policy == 'poly':
        scheduler = utils.PolyLR(optimizer, opts.total_itrs, power=0.9)
    elif opts.lr_policy == 'step':
        scheduler = torch.optim.lr_scheduler.StepLR(optimizer, step_size=opts.step_size, gamma=0.1)

    # Set up criterion
    # criterion = utils.get_loss(opts.loss_type)
    if opts.loss_type == 'focal_loss':
        criterion = utils.FocalLoss(ignore_index=255, size_average=True)
    elif opts.loss_type == 'cross_entropy':
        criterion = nn.CrossEntropyLoss(ignore_index=255, reduction='mean')

    def save_ckpt(path):
        """ save current model
        """
        torch.save({
            "cur_itrs": cur_itrs,
            "model_state": model.module.state_dict(),
            "optimizer_state": optimizer.state_dict(),
            "scheduler_state": scheduler.state_dict(),
            "best_score": best_score,
        }, path)
        print("Model saved as %s" % path)

    utils.mkdir('checkpoints')
    # Restore
    best_score = 0.0
    cur_itrs = 0
    cur_epochs = 0
    if opts.ckpt is not None and os.path.isfile(opts.ckpt):
        # https://github.com/VainF/DeepLabV3Plus-Pytorch/issues/8#issuecomment-605601402, @PytaichukBohdan
        checkpoint = torch.load(opts.ckpt, map_location=torch.device('cpu'))
        model.load_state_dict(checkpoint["model_state"])
        model = nn.DataParallel(model)
        model.to(device)
        if opts.continue_training:
            optimizer.load_state_dict(checkpoint["optimizer_state"])
            scheduler.load_state_dict(checkpoint["scheduler_state"])
            cur_itrs = checkpoint["cur_itrs"]
            best_score = checkpoint['best_score']
            print("Training state restored from %s" % opts.ckpt)
        print("Model restored from %s" % opts.ckpt)
        del checkpoint  # free memory
    else:
        print("[!] Retrain")
        model = nn.DataParallel(model)
        model.to(device)

    # ==========   Train Loop   ==========#
    vis_sample_id = np.random.randint(0, len(val_loader), opts.vis_num_samples,
                                      np.int32) if opts.enable_vis else None  # sample idxs for visualization
    denorm = utils.Denormalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225])  # denormalization for ori images

    if opts.test_only:
       #writer = SummaryWriter("/media/fahad/Crucial X8/deeplabv3plus/original_baseline/logs/R101")

        # model.eval()
        val_score, ret_samples = validate(
            opts=opts, model=model, loader=val_loader, device=device, metrics=metrics, ret_samples_ids=vis_sample_id,writer=writer)
        print(metrics.to_str(val_score))
        return

    interval_loss = 0
    while True:  # cur_itrs < opts.total_itrs:
        # =====  Train  =====
        model.train()
        cur_epochs += 1
        for (images, labels) in train_loader:
            cur_itrs += 1

            images = images.to(device, dtype=torch.float32)
            labels = labels.to(device, dtype=torch.long)

            optimizer.zero_grad()
            outputs,feat_image = model(images)
            loss = criterion(outputs, labels)
            loss.backward()
            optimizer.step()

            np_loss = loss.detach().cpu().numpy()
            interval_loss += np_loss

            if vis is not None:
                vis.vis_scalar('Loss', cur_itrs, np_loss)

            if (cur_itrs) % 10 == 0:
                interval_loss = interval_loss / 10
                print("Epoch %d, Itrs %d/%d, Loss=%f" %
                      (cur_epochs, cur_itrs, opts.total_itrs, interval_loss))
                
            if (cur_itrs) % 100 == 0: 
                interval_loss=interval_loss/100
                writer.add_scalar('train_image_loss', interval_loss, cur_itrs)
                interval_loss = 0.0
                add_gta_infos_in_tensorboard(writer,images,labels,outputs,cur_itrs,denorm,train_loader)
                writer.add_scalar('LR_Backbone',scheduler.get_lr()[0],cur_itrs)
                writer.add_scalar('LR_classifier',scheduler.get_lr()[1],cur_itrs)
                writer_add_features(writer,'feat_lowl_from_images',feat_image['low_level'],cur_itrs)
                writer_add_features(writer,'feat_out_from_images',feat_image['out'],cur_itrs)
                writer_add_features(writer,'feat_layer2_from_images',feat_image['layer2'],cur_itrs)
                writer_add_features(writer,'feat_layer3_from_images',feat_image['layer3'],cur_itrs)
                writer.add_histogram('low_feats',feat_image['low_level'],cur_itrs)
                writer.add_histogram('layer2_feats',feat_image['layer2'],cur_itrs)
                writer.add_histogram('layer3_feats',feat_image['layer3'],cur_itrs)
                writer.add_histogram('out_feats',feat_image['out'],cur_itrs)
                writer.add_scalar('mean_lowfeat', torch.mean(feat_image['low_level'][0][0]).detach().cpu().numpy(), cur_itrs)
       
                writer.add_scalar('mean_outfeat', torch.mean(feat_image['out'][0][0]).detach().cpu().numpy(), cur_itrs)
                # writer.add_scalar('mean_outfeat', torch.mean(feat_image['layer2'][0][0]).detach().cpu().numpy(), cur_itrs)
                # writer.add_scalar('mean_outfeat', torch.mean(feat_image['layer3'][0][0]).detach().cpu().numpy(), cur_itrs)
            if (cur_itrs) % opts.val_interval == 0:
                save_ckpt('checkpoints/latest_%s_%s_os%d.pth' %
                          (opts.model, opts.dataset, opts.output_stride))
                print("validation...")
                # model.eval()
                val_score, ret_samples = validate(
                    opts=opts, model=model, loader=val_loader, device=device, metrics=metrics,denorm=denorm,writer=writer,cur_itrs=cur_itrs,
                    ret_samples_ids=vis_sample_id)
                print(metrics.to_str(val_score))
                if val_score['Mean IoU'] > best_score:  # save best model
                    best_score = val_score['Mean IoU']
                    save_ckpt('checkpoints/best_%s_%s_os%d.pth' %
                              (opts.model, opts.dataset, opts.output_stride))
                writer.add_scalar('mIoU_cs', val_score['Mean IoU'], cur_itrs)
                writer.add_scalar('overall_acc_cs',val_score['Overall Acc'],cur_itrs)

                if vis is not None:  # visualize validation score and samples
                    vis.vis_scalar("[Val] Overall Acc", cur_itrs, val_score['Overall Acc'])
                    vis.vis_scalar("[Val] Mean IoU", cur_itrs, val_score['Mean IoU'])
                    vis.vis_table("[Val] Class IoU", val_score['Class IoU'])

                    for k, (img, target, lbl) in enumerate(ret_samples):
                        img = (denorm(img) * 255).astype(np.uint8)
                        target = train_dst.decode_target(target).transpose(2, 0, 1).astype(np.uint8)
                        lbl = train_dst.decode_target(lbl).transpose(2, 0, 1).astype(np.uint8)
                        concat_img = np.concatenate((img, target, lbl), axis=2)  # concat along width
                        vis.vis_image('Sample %d' % k, concat_img)
                model.train()
            scheduler.step()

            if cur_itrs >= opts.total_itrs:
                return


if __name__ == '__main__':
    main()
