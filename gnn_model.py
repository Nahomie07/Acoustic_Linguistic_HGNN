import dgl.function as fn
import torch
import torch.nn as nn
import torch.nn.functional as F
from dgl.nn import SAGEConv, GraphConv
import matplotlib.pyplot as plt
from sklearn.manifold import TSNE
from dgl.data.utils import load_graphs
import argparse 
import os
import pickle
from sklearn.preprocessing import LabelEncoder

import torch
import torch.nn as nn
import networkx as nx
import dgl
import logging
import numpy as np

class CNN(nn.Module):
    def __init__(self, conv_param, hidden_units):
        super(CNN, self).__init__()
        
        self.input_shape = conv_param[0][2]  # Save input shape for later use
        
        # Initialize the first convolutional layer
        self.conv1 = nn.Conv2d(
            in_channels=conv_param[0][0], 
            out_channels=conv_param[1], 
            kernel_size=conv_param[0][1], 
            padding=conv_param[0][1] // 2  # Manually calculating padding for 'same'
        )
        
        self.relu = nn.ReLU()
        self.pool = nn.MaxPool2d(kernel_size=conv_param[2])
        self.flatten = nn.Flatten()

        # Determine the size of the input for the linear layers
        num_conv_features = self._calculate_conv_features(conv_param)
        self.linear_layers = self._create_linear_layers(11904, hidden_units)

    def forward(self, x):
        x = self.conv1(x)
        x = self.relu(x)
        x = self.pool(x)
        x = self.flatten(x)

        # Apply the linear layers
        for layer in self.linear_layers:
            x = layer(x)
            x = self.relu(x)
        return x

    

    def _create_linear_layers(self, num_conv_features, hidden_units):
        # Adjust the first layer to match the output size from the convolutional layers
        layers = [nn.Linear(num_conv_features, hidden_units[0])]
        for i in range(1, len(hidden_units)):
           layers.append(nn.Linear(hidden_units[i-1], hidden_units[i]))
        return nn.ModuleList(layers)
    
    def _calculate_conv_features(self, conv_param):
        # Calculate based on actual input dimensions
        dummy_input = torch.zeros((1, conv_param[0][0], *self.input_shape))
        conv_output = self.conv1(dummy_input)
        conv_output = self.pool(conv_output)
        return int(np.prod(conv_output.size()))  # Total number of features after convolution and pooling



# Define the GCN model
class GCN(nn.Module):
    def __init__(self, in_feats, hidden_size, num_classes, conv_param,hidden_units):
        super(GCN, self).__init__()
        self.flatten = nn.Flatten()
        self.cnn = CNN(conv_param=conv_param, hidden_units=hidden_units)
        self.conv1 = SAGEConv(hidden_units[-1], hidden_size, 'mean') #je viens de décommenter
        #self.conv1 = SAGEConv(in_feats, hidden_size, 'mean') je viens de commenter
        self.conv2 = SAGEConv(hidden_size, num_classes, 'mean')

    def forward(self, g, features, edge_weights):
        x = self.cnn(features.unsqueeze(1)).squeeze(1) #je viens de décommenter
        #x = self.flatten(features) je viens de commenter
        x = F.relu(self.conv1(g, x, edge_weights))
        x = self.conv2(g, x, edge_weights)
        return x

# Define the training function
def train(model, g, features, edge_weights, labels, epochs=100, lr=0.01):
    optimizer = torch.optim.Adam(model.parameters(), lr=lr)
    for epoch in range(epochs):
        model.train()
        logits = model(g, features, edge_weights)
        loss = F.cross_entropy(logits, labels)
        optimizer.zero_grad()
        loss.backward()
        optimizer.step()
        if epoch % 10 == 0:
            print(f'Epoch {epoch}, Loss: {loss.item()}')
            


# Define a custom topological loss function
def topological_loss(embeddings, adj_matrix):
    # Calculate pairwise cosine similarity between embeddings
    cosine_sim = F.cosine_similarity(embeddings.unsqueeze(1), embeddings.unsqueeze(0), dim=2)
    # Zero out the diagonal of the cosine similarity matrix
    cosine_sim = cosine_sim - torch.diag_embed(torch.diag(cosine_sim))
    # Compute the reconstruction loss
    reconstruction_loss = F.mse_loss(cosine_sim, adj_matrix)
    
    return reconstruction_loss

# Define the training function with topological loss
def train_with_topological_loss(model, g, features, edge_weights,adj_matrix, epochs=100, lr=0.01):
    optimizer = torch.optim.Adam(model.parameters(), lr=lr)
    for epoch in range(epochs):
        model.train()
        embeddings = model(g, features, edge_weights)
        loss = topological_loss(embeddings, adj_matrix)
        optimizer.zero_grad()
        loss.backward()
        optimizer.step()
        if epoch % 10 == 0:
            print(f'Epoch {epoch}, Loss: {loss.item()}')

def unsupervised_loss(embeddings, g, negative_samples=5):
    positive_loss = 0
    negative_loss = 0

    for node in range(g.number_of_nodes()):
        # Positive sampling
        neighbors = g.successors(node)
        for neighbor in neighbors:
            pos_similarity = F.cosine_similarity(embeddings[node].unsqueeze(0), embeddings[neighbor].unsqueeze(0))
            positive_loss += -torch.log(torch.sigmoid(pos_similarity))

        # Negative sampling
        for _ in range(negative_samples):
            neg_node = torch.randint(0, g.number_of_nodes(), (1,))
            neg_similarity = F.cosine_similarity(embeddings[node].unsqueeze(0), embeddings[neg_node].unsqueeze(0))
            negative_loss += -torch.log(1 - torch.sigmoid(neg_similarity))

    # Sum the losses and return a scalar
    loss = (positive_loss + negative_loss) / g.number_of_nodes()
    return loss.sum()  # Ensure that the loss is a scalar


# Define the training function with GraphSAGE-like unsupervised loss
def train_with_unsupervised_loss(model, g, features, edge_weights, negative_samples=5, epochs=100, lr=0.01):
    optimizer = torch.optim.Adam(model.parameters(), lr=lr)
    for epoch in range(epochs):
        model.train()
        embeddings = model(g, features, edge_weights)
        loss = unsupervised_loss(embeddings, g, negative_samples)
        optimizer.zero_grad()
        loss.backward()
        optimizer.step()
        if epoch % 10 == 0:
            print(f'Epoch {epoch}, Loss: {loss.item()}')


if __name__ == "__main__":
	logging.info(f' ----------------------------------------------------- GNN MODEL TRAINNING STEP  -----------------------------------------------')
	parser = argparse.ArgumentParser()
	parser.add_argument('--input_folder', help ='source folder')
	parser.add_argument('--graph_file', help ='graph for trainning')
	parser.add_argument('--epochs', help='number of epochs', required=True)
	 
	args = parser.parse_args()
	input_folder = args.input_folder    
	graph_file = args.graph_file




	glist, label_dict = load_graphs(os.path.join(input_folder,graph_file))
	dgl_G = glist[0]  

	features = dgl_G.ndata['feat']
	labels = dgl_G.ndata['label']
	edge_weights = dgl_G.edata['weight']

	# Initialize the GCN model

	#in_feats = features[0].shape[0] * features[0].shape[1]
	in_feats = features[0].shape[0] 
	print(in_feats)
	hidden_size = 64
	num_classes = len(labels.unique())  # Number of unique labels
	conv_param = [
    # Parameters for the first convolutional layer
    (1, 3, (20, 64)),  # Tuple: (number of input channels, kernel size, input shape)
    32,
    # Parameters for the pooling layer
    2
]


	print(num_classes)
	hidden_units = [32, 32]
	model1 = GCN(in_feats, hidden_size, num_classes, conv_param, hidden_units)
	model2 = GCN(in_feats, hidden_size, num_classes, conv_param, hidden_units)
	model3 = GCN(in_feats, hidden_size, num_classes, conv_param, hidden_units)



	# Train the model

	train(model1, dgl_G, features,edge_weights, labels,int(args.epochs))

	# Define the file path for saving the model
	model_path = os.path.join('models',"gnn_model.pth")

	# Save the model
	torch.save(model1.state_dict(), model_path)






	# Train the model with topological loss
	# Assume adj_matrix is the adjacency matrix of the graph
	adj_matrix = torch.tensor(nx.to_numpy_matrix(dgl.to_networkx(dgl_G)))

	adj_matrix = adj_matrix.float()
	features = features.float()
	train_with_topological_loss(model2, dgl_G, features, edge_weights,adj_matrix, int(args.epochs))
	model_path_unsup = os.path.join('models',"gnn_model_unsup.pth")
	torch.save(model2.state_dict(), model_path_unsup)
	
	
	# Train the model with unsupervised loss
	#train_with_unsupervised_loss(model3, dgl_G, features, edge_weights, negative_samples=5, epochs=int(args.epochs), lr=0.01)
	# Save the model
	#model_path_unsup = os.path.join('models',"gnn_model_unsup_sage.pth")
	#torch.save(model3.state_dict(), model_path_unsup)
