import os 
import torch
import numpy as np
import torch
import torch.optim as optim
import random
import json
import time
from torch.utils.data import DataLoader
from celebA import CelebA

from torch.utils.data import DataLoader

from model import Encoder, Decoder, Classifier
from model_loss import diff_loss, recon_loss, classify_loss
from opts import parse_opts
from torchvision import transforms

class AverageMeter(object):
    """Computes and stores the average and current value"""
    def __init__(self):
        self.reset()
    
    def reset(self):
        self.val = 0
        self.avg = 0
        self.sum = 0
        self.count = 0
    
    def update(self, val, n=1):
        self.val = val
        self.sum += val * n
        self.count += n
        self.avg = self.sum / self.count
        
def bar(current, total, prefix="", suffix="", bar_sz=25, end_string=None):
    sp = ""
    print("\x1b[2K\r", end='')
    for i in range(bar_sz):
        if current * bar_sz // total > i:
            sp += '='
        elif current * bar_sz // total == i:
            sp += '>'
        else:
            sp += ' '
    if current == total:
        if end_string is None:
            print("\r%s[%s]%s" % (prefix, sp, suffix))
        else:
            if end_string != "":
                print("\r%s" % end_string)
            else:
                print("\r", end='')
    else:
        print("\r%s[%s]%s" % (prefix, sp, suffix), end='')
    
def psnr(outputs, targets):
    # [batch, channel, width, height]
    num_pixels = outputs.shape[2] * outputs.shape[3]
    batch_size = outputs.shape[0]
    seq_length = outputs.shape[1]
    psnr = torch.zeros((outputs.shape[0],outputs.shape[1]))
    for i in range(batch_size):
            mse = torch.mean((outputs[i,:,:,:] - targets[i,:,:,:])**2)
            psnr[i] = 20 * torch.log10(torch.max(outputs[i,:,:,:])) - 10 * torch.log10(mse)
    return torch.mean(psnr)


if __name__ == '__main__':
    args = parse_opts()
    # Clean the directory
    if args.clean:
        for f in ['model.pth', 'train.log', 'val.log', 'log/']:
            try:
                os.remove(os.path.join(args.save, f))
            except:
                continue

    # Check if directory exists and create one if it doesn't:
    if not os.path.isdir(args.save):
        os.makedirs(args.save)
        
    log_path = os.path.join(args.save,'log')
    if not os.path.isdir(log_path):
        os.makedirs(log_path)
    
    #split training and validation dataset
    pair_list = json.load(open(args.pair_file,'r'))
    random.shuffle(pair_list)
    train_index_list = pair_list[:int(0.8*len(pair_list))]
    eval_index_list = pair_list[int(0.8*len(pair_list)):]
    
    transform_train = transforms.Compose([
        transforms.Resize((128,128)),
        transforms.ToTensor()])
    transform_val = transforms.Compose([
        transforms.Resize((128,128)),
        transforms.ToTensor()])

    train_dataset = CelebA(args.ann_file, args.image_dir, train_index_list, transform_train, transform_train, args.att_num)
    train_loader = DataLoader(train_dataset, shuffle=True, batch_size=args.batch_size, num_workers=args.nthreads)
    val_dataset = CelebA(args.ann_file, args.image_dir, eval_index_list, transform_val, transform_val, args.att_num)
    val_loader = DataLoader(val_dataset, shuffle=True, batch_size=args.batch_size, num_workers=args.nthreads)

    print("| Data Loaded: # training data: %d, # val data: %d" % (len(train_loader)* args.batch_size, len(val_loader) * args.batch_size))

    ###############################################################################
    # Build the model
    ###############################################################################
    encoder = Encoder()
    decoder = Decoder(att_num=args.att_num)
    classifier = Classifier(args.att_num)
    
    optimizer = optim.Adam([{'params': encoder.parameters()},
                                      {'params': decoder.parameters()},
                #                      {'params': classifier.parameters()}
                          ], 
                          lr=args.lr,
                          weight_decay=args.weight_decay)

    encoder.cuda()
    decoder.cuda()
    classifier.cuda()
    ###############################################################################
    # Resume model
    ###############################################################################
    if args.resume_path:
        print('loading checkpoint {}'.format(args.resume_path))
        checkpoint = torch.load(args.resume_path)
        args.start_epoch = checkpoint['epoch']
     #   encoder.load_state_dict(checkpoint['encoder'])
     #  decoder.load_state_dict(checkpoint['decoder'])
        classifier.load_state_dict(checkpoint['classifier'])
   

    
    ###############################################################################
    # Training code
    ###############################################################################

    for epoch in range(args.epochs):
        epoch_start_time = time.time()
        loss_z = AverageMeter()
        loss_recon = AverageMeter()
        loss_classify = AverageMeter()
        loss = AverageMeter()
        encoder.train()
        decoder.train()
        classifier.train()
        for batch_idx, data in enumerate(train_loader):
            optimizer.zero_grad()
            batch_start_time = time.time()
            img_1 = data[0].cuda()
            img_2 = data[1].cuda()
            img_1_atts = data[2].cuda()
            img_2_atts = data[3].cuda() 
            
            z_1 = encoder(img_1)
            z_2 = encoder(img_2)
            img_2_trans = decoder(z_1, img_2_atts)
            img_1_trans = decoder(z_2, img_1_atts)
            img_1_recon = decoder(z_1, img_1_atts)
            img_2_recon = decoder(z_2, img_2_atts)
            img_1_atts_pre = classifier(img_1_trans)
            img_2_atts_pre = classifier(img_2_trans)
            
            
            loss_z_cur = diff_loss(z_1, z_2)
            loss_recon_cur = recon_loss(img_1_recon, img_1) +  recon_loss(img_2_recon, img_2)
            loss_classify_cur = classify_loss(img_1_atts_pre, img_1_atts) + classify_loss(img_2_atts_pre, img_2_atts)           
            
            loss_cur = loss_z_cur + args.alpha * loss_recon_cur + args.beta * loss_classify_cur
            
            loss_cur.backward()
            optimizer.step()
            
            
            loss_z.update(loss_z_cur.item())
            loss_recon.update(loss_recon_cur.item())
            loss_classify.update(loss_classify_cur.item())
            loss.update(loss_cur.item())
            batch_time = time.time() - batch_start_time         
            bar(batch_idx, len(train_loader), "Epoch: {:3d} | ".format(epoch),
            ' | time {:.3f} | loss {:.5f} | loss_z {:.5f} | loss_recon {:.5f} | loss_classify {:.5f}  |'.format(
                batch_time, loss.val, loss_z.val, loss_recon.val, loss_classify.val), end_string="")
             
        with open(os.path.join(args.save, 'train.log'), 'a') as f:
                    f.write('epoch {}:\n'.format(epoch))
                    log_entry =   ' | time {:.3f} | loss {:.5f} | loss_z {:.5f} | loss_recon {:.5f} | loss_classify {:.5f}  \n'.format(
                        time.time()-epoch_start_time, loss.avg, loss_z.avg, loss_recon.avg, loss_classify.avg)
                    f.write(log_entry)
                    

     #logger.log_scalar('train_loss',train_loss, epoch)
        with torch.no_grad():
            loss_z_val = AverageMeter()
            loss_recon_val = AverageMeter()
            loss_classify_val = AverageMeter()
            loss_val = AverageMeter()
            psnr_val = AverageMeter()
            encoder.eval()
            decoder.eval()
            classifier.eval()
            for batch_idx, data in enumerate(val_loader):
                batch_start_time = time.time()
                img_1 = data[0].cuda()
                img_2 = data[1].cuda()
                img_1_atts = data[2].cuda()
                img_2_atts = data[3].cuda() 

                z_1 = encoder(img_1)
                z_2 = encoder(img_2)
                img_2_trans = decoder(z_1, img_2_atts)
                img_1_trans = decoder(z_2, img_1_atts)
                img_1_recon = decoder(z_1, img_1_atts)
                img_2_recon = decoder(z_2, img_2_atts)
                img_1_atts_pre = classifier(img_1_trans)
                img_2_atts_pre = classifier(img_2_trans)

                loss_z_cur = diff_loss(z_1, z_2)
                loss_recon_cur = recon_loss(img_1_recon, img_1) +  recon_loss(img_2_recon, img_2)
                loss_classify_cur = classify_loss(img_1_atts_pre, img_1_atts) + classify_loss(img_2_atts_pre, img_2_atts)           

                loss_cur = loss_z_cur + args.alpha * loss_recon_cur + args.beta * loss_classify_cur
                psnr_cur = psnr(img_1_recon, img_1) + psnr(img_2_recon, img_2)
                psnr_val.update(psnr_cur)


                loss_z_val.update(loss_z_cur.item())
                loss_recon_val.update(loss_recon_cur.item())
                loss_classify_val.update(loss_classify_cur.item())
                loss_val.update(loss_cur.item())
                batch_time = time.time() - batch_start_time         
                bar(batch_idx, len(val_loader), "Epoch: {:3d} | ".format(epoch),
                ' | time {:.3f} | loss_val {:.5f} | loss_z_val {:.5f} | loss_recon_val {:.5f} | loss_classify_val {:.5f}  |'.format(
                    batch_time, loss_val.val, loss_z_val.val, loss_recon_val.val, loss_classify_val.val), end_string="")

            log_entry_val = '\n| end of epoch {:3d} | time: {:5.5f}s | valid loss {:.5f} | valid recon loss {:.5f} | valid classify loss {:.5f} | valid psnr {:5.2f}'.format(
                epoch, (time.time() - epoch_start_time),loss_val.avg, loss_recon_val.avg, loss_classify_val.avg, psnr_val.avg)
            print(log_entry_val)
            with open(os.path.join(args.save, 'val.log'), 'a') as f:
                        f.write(log_entry_val)
        
        if epoch%args.save_every == 0:
            states = {
                         'epoch': epoch,
                         'encoder': encoder.state_dict(),
                         'decoder': decoder.state_dict(),
                         'classifier': classifier.state_dict()}
            torch.save(states, os.path.join(args.save, 'checkpoint_' + str(epoch) + '.pth'))
            
            
            
        